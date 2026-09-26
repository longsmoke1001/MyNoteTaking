"""Tests for the translation feature.

Everything here runs against a temporary SQLite database and a stubbed HTTP
session, so the suite never touches the network or your real notes.
"""
import os
import tempfile

import pytest
import requests

# Point the app at a throwaway database before it is imported.
_TMP_DB = os.path.join(tempfile.gettempdir(), 'notetaker_test.db')
os.environ['DATABASE_PATH'] = _TMP_DB

from src.main import app  # noqa: E402
from src.models.note import Note, db  # noqa: E402
from src.services import translator as translator_service  # noqa: E402


@pytest.fixture
def client():
    """A Flask test client with an empty database."""
    with app.app_context():
        db.drop_all()
        db.create_all()
    with app.test_client() as test_client:
        yield test_client
    with app.app_context():
        db.drop_all()


@pytest.fixture
def mock_translator(monkeypatch):
    """Replace the real translator so no request ever leaves the machine."""
    mock = translator_service.MockTranslator()
    monkeypatch.setattr(translator_service, 'get_translator', lambda: mock)
    return mock


@pytest.fixture
def note():
    """A saved note to translate."""
    with app.app_context():
        record = Note(title='Groceries', content='Milk\nEggs\nBread')
        db.session.add(record)
        db.session.commit()
        note_id = record.id
    return note_id


# --- /api/languages --------------------------------------------------------


def test_languages_lists_options(client):
    response = client.get('/api/languages')

    assert response.status_code == 200
    payload = response.get_json()
    codes = {lang['code'] for lang in payload['languages']}
    assert {'en', 'es', 'zh', 'fr'} <= codes
    assert 'model' in payload


def test_languages_reports_configuration(client, monkeypatch):
    monkeypatch.setenv('AI_API_KEY', '')
    assert client.get('/api/languages').get_json()['configured'] is False

    monkeypatch.setenv('AI_API_KEY', 'sk-test')
    assert client.get('/api/languages').get_json()['configured'] is True


# --- /api/translate --------------------------------------------------------


def test_translate_text(client, mock_translator):
    response = client.post('/api/translate', json={'text': 'Hello', 'target_lang': 'es'})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['translated_text'] == '[Spanish] Hello'
    assert payload['target_lang'] == 'es'
    assert mock_translator.calls[0]['text'] == 'Hello'


def test_translate_preserves_line_structure(client, mock_translator):
    response = client.post(
        '/api/translate',
        json={'text': 'one\ntwo\n\nthree', 'target_lang': 'fr'},
    )

    assert response.status_code == 200
    # Blank lines stay blank so lists and paragraphs render correctly.
    assert response.get_json()['translated_text'] == '[French] one\n[French] two\n\n[French] three'


def test_translate_requires_text(client, mock_translator):
    assert client.post('/api/translate', json={'target_lang': 'es'}).status_code == 400
    assert client.post('/api/translate', json={'text': '  ', 'target_lang': 'es'}).status_code == 400


def test_translate_rejects_unknown_language(client, mock_translator):
    response = client.post('/api/translate', json={'text': 'Hello', 'target_lang': 'klingon'})

    assert response.status_code == 400
    assert response.get_json()['code'] == 'unsupported_language'


def test_translate_handles_missing_body(client, mock_translator):
    response = client.post('/api/translate', json=None)

    assert response.status_code == 400
    assert response.get_json()['code'] == 'missing_text'


def test_translate_surfaces_service_errors(client, monkeypatch):
    error = translator_service.TranslationError('The model is rate limited.', 429, 'rate_limited')
    monkeypatch.setattr(
        translator_service, 'get_translator', lambda: translator_service.MockTranslator(error)
    )

    response = client.post('/api/translate', json={'text': 'Hello', 'target_lang': 'es'})

    assert response.status_code == 429
    assert response.get_json()['error'] == 'The model is rate limited.'
    assert response.get_json()['code'] == 'rate_limited'


def test_translate_reports_missing_configuration(client, monkeypatch):
    monkeypatch.setenv('AI_API_KEY', '')
    translator_service.reset_translator()

    response = client.post('/api/translate', json={'text': 'Hello', 'target_lang': 'es'})

    assert response.status_code == 503
    assert response.get_json()['code'] == 'not_configured'


# --- /api/notes/<id>/translate ---------------------------------------------


def test_translate_note(client, mock_translator, note):
    response = client.post(f'/api/notes/{note}/translate', json={'target_lang': 'de'})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['note_id'] == note
    assert payload['title'] == '[German] Groceries'
    assert payload['content'] == '[German] Milk\n[German] Eggs\n[German] Bread'


def test_translate_note_never_writes_to_the_database(client, mock_translator, note):
    """The whole point of a preview: the stored note must be untouched."""
    before = client.get(f'/api/notes/{note}').get_json()

    client.post(f'/api/notes/{note}/translate', json={'target_lang': 'de'})

    after = client.get(f'/api/notes/{note}').get_json()
    assert after['title'] == before['title'] == 'Groceries'
    assert after['content'] == before['content'] == 'Milk\nEggs\nBread'
    assert after['updated_at'] == before['updated_at']


def test_translate_missing_note_returns_404(client, mock_translator):
    response = client.post('/api/notes/9999/translate', json={'target_lang': 'de'})

    assert response.status_code == 404


# --- chunking --------------------------------------------------------------


def test_chunking_splits_long_text_and_keeps_paragraphs():
    text = '\n\n'.join('word ' * 200 for _ in range(10))

    chunks = translator_service._chunk_text(text, max_chars=500)

    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk, _ in chunks)
    # Everything is still there once the pieces are glued back together.
    rebuilt = ''.join(chunk + (separator if index < len(chunks) - 1 else '')
                      for index, (chunk, separator) in enumerate(chunks))
    assert rebuilt.split() == text.split()


def test_chunking_keeps_short_text_as_one_chunk():
    chunks = translator_service._chunk_text('just a line')

    # Short text stays whole, and the chunk does not embed the separator that
    # follows it because the caller re-joins with it.
    assert chunks == [('just a line', '\n')]


def test_translator_wraps_an_oversized_single_paragraph():
    chunks = translator_service._chunk_text('word ' * 500, max_chars=200)

    assert len(chunks) > 1
    assert all(len(chunk) <= 200 for chunk, _ in chunks)


def test_translator_rejects_text_over_the_limit():
    translator = translator_service.OpenRouterTranslator(api_key='sk-test')

    with pytest.raises(translator_service.TranslationError) as error:
        translator.translate('x' * (translator_service.MAX_TEXT_CHARS + 1), 'es')

    assert error.value.code == 'text_too_long'


# --- response cleanup ------------------------------------------------------


def test_clean_output_strips_code_fences():
    assert translator_service._clean_output('```text\nHola\n```') == 'Hola'


def test_clean_output_strips_preamble():
    assert translator_service._clean_output('Here is the translation:\n\nHola') == 'Hola'


def test_clean_output_keeps_ordinary_text():
    assert translator_service._clean_output('Hola\n\nAdios') == 'Hola\n\nAdios'


# --- combined title + body -------------------------------------------------


def test_split_marked_output_separates_title_and_body():
    raw = (
        f'{translator_service.TITLE_MARKER}\n'
        'Titulo traducido\n'
        f'{translator_service.CONTENT_MARKER}\n'
        'Cuerpo traducido\n\nSegunda linea'
    )

    title, content = translator_service._split_marked_output(raw)

    assert title == 'Titulo traducido'
    assert content == 'Cuerpo traducido\n\nSegunda linea'


def test_split_marked_output_falls_back_when_markers_are_lost():
    """A model that ignores the markers still yields readable content."""
    title, content = translator_service._split_marked_output('Solo el cuerpo')

    assert title == ''
    assert content == 'Solo el cuerpo'


def test_split_marked_output_ignores_reversed_markers():
    raw = f'{translator_service.CONTENT_MARKER}\nc\n{translator_service.TITLE_MARKER}\nt'

    assert translator_service._split_marked_output(raw) == ('', raw)


def test_translate_text_and_title_in_one_request(client, mock_translator):
    response = client.post('/api/translate', json={
        'title': 'Groceries', 'text': 'Milk', 'target_lang': 'it',
    })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['translated_title'] == '[Italian] Groceries'
    assert payload['translated_text'] == '[Italian] Milk'


def test_translate_without_a_title_leaves_it_empty(client, mock_translator):
    payload = client.post(
        '/api/translate', json={'text': 'Milk', 'target_lang': 'it'}
    ).get_json()

    assert payload['translated_title'] == ''
    assert payload['translated_text'] == '[Italian] Milk'


def test_translate_accepts_a_title_with_empty_body(client, mock_translator):
    response = client.post('/api/translate', json={
        'title': 'Groceries', 'text': '', 'target_lang': 'it',
    })

    assert response.status_code == 200
    assert response.get_json()['translated_title'] == '[Italian] Groceries'


def test_translate_rejects_a_body_with_no_title_and_no_text(client, mock_translator):
    response = client.post('/api/translate', json={'text': '   ', 'title': '  '})

    assert response.status_code == 400
    assert response.get_json()['code'] == 'missing_text'


# --- the real translator, with a stubbed HTTP session ----------------------


class FakeResponse:
    """Just enough of a requests.Response for the translator."""

    def __init__(self, payload=None, status_code=200, headers=None, text=''):
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self.payload is None:
            raise ValueError('no json body')
        return self.payload


def completion(content, finish_reason='stop'):
    return FakeResponse({
        'choices': [{'message': {'content': content}, 'finish_reason': finish_reason}]
    })


class FakeSession:
    """Records requests and replays queued responses or exceptions."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({'url': url, 'headers': headers, 'json': json})
        if not self.responses:
            raise AssertionError('the translator made more requests than expected')
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


@pytest.fixture
def no_sleep(monkeypatch):
    """Keep retry backoff from actually sleeping during tests."""
    monkeypatch.setattr(translator_service.time, 'sleep', lambda seconds: None)


def make_translator(session, **kwargs):
    kwargs.setdefault('max_retries', 2)
    return translator_service.OpenRouterTranslator(
        api_key='sk-test', model='test/model', session=session, **kwargs
    )


def test_real_translator_sends_the_expected_request():
    session = FakeSession(completion('Hola'))
    make_translator(session).translate('Hello', 'es')

    call = session.calls[0]
    assert call['url'] == 'https://openrouter.ai/api/v1/chat/completions'
    assert call['headers']['Authorization'] == 'Bearer sk-test'
    assert call['json']['model'] == 'test/model'
    assert call['json']['messages'][0]['role'] == 'system'
    assert 'Spanish' in call['json']['messages'][1]['content']


def test_title_and_body_travel_in_one_request():
    """The rate limit only has to be survived once, not twice."""
    session = FakeSession(completion(
        f'{translator_service.TITLE_MARKER}\nTitulo\n'
        f'{translator_service.CONTENT_MARKER}\nCuerpo'
    ))

    title, content = make_translator(session).translate_fields('Title', 'Body', 'es')

    assert len(session.calls) == 1
    assert title == 'Titulo'
    assert content == 'Cuerpo'


def test_rate_limit_is_retried_then_succeeds(no_sleep):
    session = FakeSession(
        FakeResponse({'error': {'message': 'slow down'}}, 429, {'Retry-After': '1'}),
        completion('Hola'),
    )

    assert make_translator(session).translate('Hello', 'es') == 'Hola'
    assert len(session.calls) == 2


def test_rate_limit_gives_up_with_an_actionable_error(no_sleep):
    session = FakeSession(*[FakeResponse({'error': {'message': 'slow down'}}, 429)] * 3)

    with pytest.raises(translator_service.TranslationError) as error:
        make_translator(session).translate('Hello', 'es')

    assert error.value.code == 'rate_limited'
    assert error.value.status_code == 429
    # The message must name the model so the fix is obvious.
    assert 'test/model' in error.value.message
    assert 'AI_MODEL' in error.value.message
    assert len(session.calls) == 3  # initial attempt plus two retries


def test_truncated_answer_is_retried_with_a_larger_budget(no_sleep):
    session = FakeSession(
        completion('parcial', finish_reason='length'),
        completion('Hola'),
    )

    assert make_translator(session).translate('Hello', 'es') == 'Hola'

    first, second = session.calls[0]['json'], session.calls[1]['json']
    assert second['max_tokens'] > first['max_tokens']


def test_persistently_truncated_answer_reports_an_error(no_sleep):
    session = FakeSession(*[completion('parcial', finish_reason='length')] * 4)

    with pytest.raises(translator_service.TranslationError) as error:
        make_translator(session).translate('Hello', 'es')

    assert error.value.code == 'output_truncated'


def test_bad_api_key_fails_immediately(no_sleep):
    session = FakeSession(FakeResponse({'error': {'message': 'no key'}}, 401))

    with pytest.raises(translator_service.TranslationError) as error:
        make_translator(session).translate('Hello', 'es')

    assert error.value.code == 'invalid_api_key'
    assert len(session.calls) == 1  # retrying a bad key would be pointless


def test_network_failure_is_retried(no_sleep):
    session = FakeSession(
        requests.RequestException('connection reset'),
        completion('Hola'),
    )

    assert make_translator(session).translate('Hello', 'es') == 'Hola'
    assert len(session.calls) == 2


def test_persistent_network_failure_reports_an_error(no_sleep):
    session = FakeSession(*[requests.RequestException('down')] * 3)

    with pytest.raises(translator_service.TranslationError) as error:
        make_translator(session).translate('Hello', 'es')

    assert error.value.code == 'network_error'


def test_server_error_is_retried(no_sleep):
    session = FakeSession(FakeResponse({}, 503), completion('Hola'))

    assert make_translator(session).translate('Hello', 'es') == 'Hola'


def test_empty_completion_is_reported(no_sleep):
    session = FakeSession(completion('   '))

    with pytest.raises(translator_service.TranslationError) as error:
        make_translator(session).translate('Hello', 'es')

    assert error.value.code == 'empty_response'


def test_blocked_content_surfaces_the_provider_message(no_sleep):
    session = FakeSession(FakeResponse(
        {'error': {'message': 'this model is not allowed'}}, 403
    ))

    with pytest.raises(translator_service.TranslationError) as error:
        make_translator(session).translate('Hello', 'es')

    assert error.value.code == 'invalid_api_key'


def test_already_target_language_short_circuits():
    """No request at all when the source and target match."""
    session = FakeSession()

    result = make_translator(session).translate('Hola', 'es', source_lang='es')

    assert result == 'Hola'
    assert session.calls == []


def test_long_text_is_split_across_requests(no_sleep):
    text = '\n\n'.join('palabra ' * 150 for _ in range(4))
    chunks = translator_service._chunk_text(text)
    assert len(chunks) > 1, 'expected this text to need chunking'

    session = FakeSession(*[completion(f'parte{index}') for index in range(len(chunks))])
    result = make_translator(session).translate(text, 'es')

    # One request per chunk, with the original separators restored between them.
    assert len(session.calls) == len(chunks)
    assert result == '\n\n'.join(f'parte{index}' for index in range(len(chunks)))


def test_oversized_paragraph_is_reflowed_before_sending(no_sleep):
    """A hard wrapped paragraph must not reach the model with its wrapping."""
    wrapped = ' '.join(['palabra'] * 900)  # one long line, no line breaks at all
    chunks = translator_service._chunk_text(wrapped)
    assert len(chunks) > 1, 'expected the long line to be split'

    # Every chunk is a single line, so the model cannot copy column wrapping.
    for chunk, _ in chunks:
        assert '\n' not in chunk

    session = FakeSession(*[completion(f'parte{index}') for index in range(len(chunks))])
    make_translator(session).translate(wrapped, 'es')

    assert len(session.calls) == len(chunks)


def test_provider_content_blocks_are_joined():
    session = FakeSession(FakeResponse({
        'choices': [{
            'message': {'content': [{'text': 'Hola '}, {'text': 'mundo'}]},
            'finish_reason': 'stop',
        }]
    }))

    assert make_translator(session).translate('Hello world', 'es') == 'Hola mundo'

