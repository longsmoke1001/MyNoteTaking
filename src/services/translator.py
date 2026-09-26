"""Note translation service.

Talks to an OpenAI-compatible chat completions endpoint (OpenRouter by default)
using the credentials in the project `.env` file (`AI_API_KEY` / `AI_MODEL`).

This module is deliberately free of any database import. Text goes in and
translated text comes back, so the whole translation path runs unchanged on a
stateless serverless host such as Vercel, where no database is available and
the filesystem is read-only. Translating is strictly read-only as well: nothing
here can write to a database, so a translated note can never overwrite the
original.
"""
import os
import random
import re
import time

import requests

# --- Configuration ---------------------------------------------------------

DEFAULT_BASE_URL = 'https://openrouter.ai/api/v1'
DEFAULT_MODEL = 'nvidia/nemotron-3.5-lightning:free'

# Free tier models are rate limited constantly, so every call is retried with an
# exponential backoff before giving up.
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 3.0
BACKOFF_CAP_SECONDS = 30.0
REQUEST_TIMEOUT_SECONDS = 90

# Long notes are split so a single request stays well inside a small model's
# context window.
MAX_CHUNK_CHARS = 3000
MAX_TEXT_CHARS = 20000

# Output budget, doubled on demand when the model reports a truncated answer.
MIN_OUTPUT_TOKENS = 2000
MAX_OUTPUT_TOKENS = 8000

SYSTEM_PROMPT = (
    "You are a professional translator. Translate the user's text into the "
    "requested target language.\n"
    "Rules:\n"
    "- Output ONLY the translation. No preamble, no notes, no explanations.\n"
    "- Preserve the structure exactly as written: line breaks, blank lines, "
    "indentation, bullet points and numbered lists.\n"
    "- Never add, drop, merge or re-wrap lines.\n"
    "- Keep proper nouns, code identifiers, URLs, numbers and units untouched.\n"
    "- If the text is already in the target language, return it unchanged.\n"
    "- Detect the source language automatically."
)

# A note's title and body are translated in a single request. These marker lines
# keep the two apart, which halves the latency and, on a rate limited model,
# roughly halves the chance of the request being turned away.
TITLE_MARKER = '<<<TITLE>>>'
CONTENT_MARKER = '<<<CONTENT>>>'

MARKED_SYSTEM_PROMPT = SYSTEM_PROMPT + (
    f'\n- The text contains the marker lines "{TITLE_MARKER}" and "{CONTENT_MARKER}".\n'
    f'- Reproduce each marker line exactly and unchanged, and translate only the '
    'text that follows it.\n'
)

# Languages offered in the UI. Codes are ISO 639-1.
LANGUAGES = [
    {'code': 'ar', 'name': 'Arabic'},
    {'code': 'bn', 'name': 'Bengali'},
    {'code': 'zh', 'name': 'Chinese (Simplified)'},
    {'code': 'zh-TW', 'name': 'Chinese (Traditional)'},
    {'code': 'cs', 'name': 'Czech'},
    {'code': 'da', 'name': 'Danish'},
    {'code': 'nl', 'name': 'Dutch'},
    {'code': 'en', 'name': 'English'},
    {'code': 'fi', 'name': 'Finnish'},
    {'code': 'fr', 'name': 'French'},
    {'code': 'de', 'name': 'German'},
    {'code': 'el', 'name': 'Greek'},
    {'code': 'he', 'name': 'Hebrew'},
    {'code': 'hi', 'name': 'Hindi'},
    {'code': 'hu', 'name': 'Hungarian'},
    {'code': 'id', 'name': 'Indonesian'},
    {'code': 'it', 'name': 'Italian'},
    {'code': 'ja', 'name': 'Japanese'},
    {'code': 'ko', 'name': 'Korean'},
    {'code': 'lv', 'name': 'Latvian'},
    {'code': 'lt', 'name': 'Lithuanian'},
    {'code': 'ms', 'name': 'Malay'},
    {'code': 'no', 'name': 'Norwegian'},
    {'code': 'pl', 'name': 'Polish'},
    {'code': 'pt', 'name': 'Portuguese'},
    {'code': 'ro', 'name': 'Romanian'},
    {'code': 'ru', 'name': 'Russian'},
    {'code': 'es', 'name': 'Spanish'},
    {'code': 'sv', 'name': 'Swedish'},
    {'code': 'th', 'name': 'Thai'},
    {'code': 'tr', 'name': 'Turkish'},
    {'code': 'uk', 'name': 'Ukrainian'},
    {'code': 'vi', 'name': 'Vietnamese'},
]
LANGUAGE_NAMES = {lang['code']: lang['name'] for lang in LANGUAGES}


class TranslationError(Exception):
    """A translation failure that is safe to show to the user."""

    def __init__(self, message, status_code=502, code='translation_failed'):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def resolve_language(code):
    """Map a language code to its display name, rejecting unknown codes."""
    name = LANGUAGE_NAMES.get((code or '').strip())
    if not name:
        raise TranslationError(
            f'Unsupported language code "{code}".', 400, 'unsupported_language'
        )
    return name


# --- Text chunking ---------------------------------------------------------


def _hard_wrap(text, max_chars):
    """Split one oversized segment into word wrapped pieces."""
    lines, current = [], ''
    for word in text.split():
        if current and len(current) + 1 + len(word) > max_chars:
            lines.append(current)
            current = word
        else:
            current = f'{current} {word}' if current else word
    if current:
        lines.append(current)
    return lines or [text[:max_chars]]


def _chunk_text(text, max_chars=MAX_CHUNK_CHARS):
    """Split text into `(chunk, following_separator)` pairs.

    Splitting happens on line boundaries so paragraphs and lists survive the
    round trip. A chunk never embeds the separator that follows it, because the
    caller re-joins the translated pieces with it, which keeps every chunk
    strictly within `max_chars`.
    """
    raw = re.split(r'(\n+)', text)
    segments = []
    for index in range(0, len(raw), 2):
        content = raw[index]
        separator = raw[index + 1] if index + 1 < len(raw) else ''
        if not content.strip():
            continue
        separator = separator or '\n'

        if len(content) <= max_chars:
            segments.append((content, separator))
            continue

        # A hard wrapped paragraph is re-flowed into single lines first, so the
        # model does not copy the original column wrapping into its output.
        pieces = _hard_wrap(content, max_chars)
        for position, piece in enumerate(pieces):
            trailing = '\n' if position < len(pieces) - 1 else separator
            segments.append((piece, trailing))

    chunks = []
    current, current_separator = '', ''
    for content, separator in segments:
        joiner = current_separator if current else ''
        if current and len(current) + len(joiner) + len(content) > max_chars:
            chunks.append((current, current_separator))
            current, current_separator, joiner = '', '', ''
        current += joiner + content
        current_separator = separator

    if current:
        chunks.append((current, current_separator))
    return chunks


_PREAMBLES = (
    'here is the translation:',
    "here's the translation:",
    'the translation is:',
    'translated text:',
    'translation:',
    '译文：',
    '翻译：',
    '翻译结果：',
)


def _clean_output(text):
    """Strip code fences and chatty preambles a model may add anyway."""
    cleaned = text.strip()

    fence = re.match(r'^```[a-zA-Z]*\s*\n(.*?)\n?```$', cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()

    lowered = cleaned.lower()
    for preamble in _PREAMBLES:
        if lowered.startswith(preamble):
            cleaned = cleaned[len(preamble):].strip()
            break

    return cleaned


def _split_marked_output(raw):
    """Split a combined translation back into a `(title, content)` pair.

    If the model dropped or mangled the markers the whole reply is treated as
    content, so a sloppy answer still shows the user something readable.
    """
    text = raw.strip()
    title_at = text.find(TITLE_MARKER)
    content_at = text.find(CONTENT_MARKER)

    if title_at == -1 or content_at == -1 or content_at < title_at:
        return '', text

    title = text[title_at + len(TITLE_MARKER):content_at]
    content = text[content_at + len(CONTENT_MARKER):]
    return title.strip(), content.strip()


# --- Translator ------------------------------------------------------------


class OpenRouterTranslator:
    """Translates text through a chat completions endpoint."""

    def __init__(self, api_key, model=None, base_url=None, session=None,
                 max_retries=MAX_RETRIES):
        self.api_key = api_key
        self.model = (model or os.getenv('AI_MODEL') or DEFAULT_MODEL).strip()
        self.base_url = (base_url or os.getenv('AI_BASE_URL') or DEFAULT_BASE_URL).rstrip('/')
        self.max_retries = max_retries
        self.session = session or requests.Session()

    # -- public API

    def translate(self, text, target_lang, source_lang='auto'):
        """Translate `text` into `target_lang`, chunking long input if needed."""
        target_name = resolve_language(target_lang)

        if not text or not text.strip():
            return text or ''

        self._guard_length(text)

        source = (source_lang or 'auto').strip()
        if source != 'auto' and source == target_lang.strip():
            return text

        chunks = _chunk_text(text)
        if not chunks:
            return text

        parts = []
        for index, (chunk, separator) in enumerate(chunks):
            parts.append(self._translate_chunk(chunk, target_name, source))
            if index < len(chunks) - 1:
                parts.append(separator)
        return ''.join(parts).strip()

    def translate_fields(self, title, content, target_lang, source_lang='auto'):
        """Translate a title and its body in a single request."""
        target_name = resolve_language(target_lang)
        title = title or ''
        content = content or ''

        if not title.strip() and not content.strip():
            return '', content

        self._guard_length(title, content)

        source = (source_lang or 'auto').strip()
        if source != 'auto' and source == target_lang.strip():
            return title, content

        # With only one field present there is nothing to keep apart.
        if not title.strip():
            return '', self.translate(content, target_lang, source)
        if not content.strip():
            return self.translate(title, target_lang, source), ''

        raw = self._complete(
            [
                {'role': 'system', 'content': MARKED_SYSTEM_PROMPT},
                {
                    'role': 'user',
                    'content': (
                        f'Target language: {target_name}\n\n'
                        f'{TITLE_MARKER}\n{title}\n{CONTENT_MARKER}\n{content}'
                    ),
                },
            ]
        )
        return _split_marked_output(_clean_output(raw))

    # -- internals

    def _guard_length(self, *texts):
        for value in texts:
            if len(value or '') > MAX_TEXT_CHARS:
                raise TranslationError(
                    f'Text is too long to translate ({len(value)} characters, '
                    f'limit {MAX_TEXT_CHARS}).',
                    413,
                    'text_too_long',
                )

    def _endpoint(self):
        return f'{self.base_url}/chat/completions'

    def _headers(self):
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
            'X-Title': os.getenv('AI_APP_TITLE', 'NoteTaker'),
        }
        referer = os.getenv('AI_HTTP_REFERER')
        if referer:
            headers['HTTP-Referer'] = referer
        return headers

    def _translate_chunk(self, chunk, target_name, source_lang):
        instruction = f'Target language: {target_name}'
        if source_lang and source_lang != 'auto':
            instruction += f'\nSource language: {resolve_language(source_lang)}'
        instruction += f'\n\nText to translate:\n"""\n{chunk}\n"""'

        raw = self._complete(
            [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': instruction},
            ]
        )
        return _clean_output(raw)

    def _complete(self, messages):
        """Run a chat completion, retrying rate limits and recovering from truncation."""
        tokens = MIN_OUTPUT_TOKENS
        attempt = 0

        while True:
            try:
                response = self.session.post(
                    self._endpoint(),
                    headers=self._headers(),
                    json={
                        'model': self.model,
                        'messages': messages,
                        'temperature': 0.2,
                        'max_tokens': tokens,
                    },
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise TranslationError(
                        f'Could not reach the translation service: {exc}', 502, 'network_error'
                    )
                self._sleep(attempt)
                attempt += 1
                continue

            if response.status_code == 200:
                content, finish_reason = self._read_completion(response)

                if finish_reason == 'length':
                    # The answer was cut off. Ask again with a bigger budget.
                    if tokens < MAX_OUTPUT_TOKENS:
                        tokens = min(MAX_OUTPUT_TOKENS, tokens * 2)
                        continue
                    raise TranslationError(
                        'The translation was cut off because it was too long. '
                        'Try translating a shorter note.',
                        502,
                        'output_truncated',
                    )

                if not content:
                    raise TranslationError(
                        'The translation service returned an empty response.', 502, 'empty_response'
                    )
                return content

            if response.status_code in (401, 403):
                raise TranslationError(
                    'The translation service rejected the API key. Check AI_API_KEY '
                    'in your .env file.',
                    502,
                    'invalid_api_key',
                )

            if response.status_code == 429:
                if attempt >= self.max_retries:
                    raise TranslationError(self._rate_limit_message(response), 429, 'rate_limited')
                self._sleep(attempt, response)
                attempt += 1
                continue

            if response.status_code >= 500:
                if attempt >= self.max_retries:
                    raise TranslationError(
                        f'The translation service is unavailable (HTTP {response.status_code}).',
                        502,
                        'upstream_error',
                    )
                self._sleep(attempt, response)
                attempt += 1
                continue

            raise TranslationError(
                f'The translation service rejected the request '
                f'(HTTP {response.status_code}): {self._error_detail(response)}',
                502,
                'upstream_error',
            )

    def _read_completion(self, response):
        try:
            payload = response.json()
        except ValueError:
            raise TranslationError(
                'The translation service returned an unreadable response.', 502, 'bad_response'
            )

        choices = payload.get('choices') or []
        if not choices:
            raise TranslationError(
                f'The translation service returned no result: {self._error_detail(response)}',
                502,
                'bad_response',
            )

        choice = choices[0]
        content = (choice.get('message') or {}).get('content')
        if isinstance(content, list):
            # Some providers stream style content as a list of blocks.
            content = ''.join(
                block.get('text', '') for block in content if isinstance(block, dict)
            )
        return (content or '').strip(), choice.get('finish_reason')

    def _rate_limit_message(self, response):
        retry_after = (response.headers.get('Retry-After') or '').strip()
        if retry_after.isdigit():
            wait = f' Try again in {retry_after} seconds.'
        else:
            wait = ' Try again in a minute.'
        return (
            f'The model "{self.model}" is rate limited by the translation service.{wait} '
            f'You can choose a different one with AI_MODEL in your .env file.'
        )

    def _error_detail(self, response):
        try:
            error = (response.json() or {}).get('error')
        except ValueError:
            return (response.text or '')[:200]
        if isinstance(error, dict):
            return str(error.get('message') or error)[:300]
        return str(error or response.text or '')[:300]

    def _sleep(self, attempt, response=None):
        delay = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** attempt))
        if response is not None:
            retry_after = (response.headers.get('Retry-After') or '').strip()
            if retry_after.isdigit():
                delay = max(delay, min(int(retry_after), BACKOFF_CAP_SECONDS))
        time.sleep(delay + random.uniform(0, 1))


class MockTranslator:
    """Deterministic stand-in used by the tests so they never touch the network."""

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def translate(self, text, target_lang, source_lang='auto'):
        target_name = resolve_language(target_lang)
        self.calls.append({'text': text, 'target_lang': target_lang, 'source_lang': source_lang})
        if self.error:
            raise self.error
        if not text or not text.strip():
            return text or ''
        prefix = f'[{target_name}] '
        return '\n'.join(prefix + line if line.strip() else line for line in text.split('\n'))

    def translate_fields(self, title, content, target_lang, source_lang='auto'):
        return (
            self.translate(title, target_lang, source_lang) if title else '',
            self.translate(content, target_lang, source_lang) if content else '',
        )


# --- Module level access ---------------------------------------------------

_translator = None


def is_configured():
    """True when an API key is available for translation."""
    return bool((os.getenv('AI_API_KEY') or '').strip())


def config_summary():
    """Non-secret description of the translation setup, for the client."""
    return {
        'configured': is_configured(),
        'model': (os.getenv('AI_MODEL') or DEFAULT_MODEL).strip(),
        'base_url': (os.getenv('AI_BASE_URL') or DEFAULT_BASE_URL).rstrip('/'),
    }


def get_translator():
    """Return the shared translator, building it on first use."""
    global _translator
    if _translator is None:
        if not is_configured():
            raise TranslationError(
                'Translation is not configured. Add AI_API_KEY to your .env file.',
                503,
                'not_configured',
            )
        _translator = OpenRouterTranslator(os.getenv('AI_API_KEY').strip())
    return _translator


def reset_translator():
    """Drop the cached translator (used by the tests)."""
    global _translator
    _translator = None
