from flask import Blueprint, jsonify, request

from src.services import translator as translator_service

# This blueprint is deliberately free of any database access. The text to
# translate arrives in the request body, so every endpoint here works on a
# stateless serverless filesystem (Vercel) with no writable storage and no
# database driver loaded.
translate_bp = Blueprint('translate', __name__)


def _error(exc):
    """Render a TranslationError as a JSON response."""
    return jsonify({'error': exc.message, 'code': exc.code}), exc.status_code


def _payload():
    return request.get_json(silent=True) or {}


def _requested_languages(data):
    target = (data.get('target_lang') or data.get('target') or '').strip()
    source = (data.get('source_lang') or data.get('source') or 'auto').strip() or 'auto'
    return source, target


@translate_bp.route('/languages', methods=['GET'])
def get_languages():
    """List the languages offered in the UI plus the current model."""
    return jsonify({
        'languages': translator_service.LANGUAGES,
        **translator_service.config_summary(),
    })


@translate_bp.route('/translate', methods=['POST'])
def translate_text():
    """Translate a snippet of text, optionally alongside its title.

    A title is sent in the same request as the body rather than as a second
    call, so a rate limited model only has to answer once.
    """
    data = _payload()
    text = data.get('text')
    title = data.get('title') or ''

    if not isinstance(text, str) or (not text.strip() and not str(title).strip()):
        return jsonify({'error': 'Text is required.', 'code': 'missing_text'}), 400

    source, target = _requested_languages(data)

    try:
        translator = translator_service.get_translator()
        if str(title).strip():
            translated_title, translated_text = translator.translate_fields(
                title, text, target, source
            )
        else:
            translated_title, translated_text = '', translator.translate(text, target, source)
    except translator_service.TranslationError as exc:
        return _error(exc)

    return jsonify({
        'translated_title': translated_title,
        'translated_text': translated_text,
        'source_lang': source,
        'target_lang': target,
        'model': translator_service.config_summary()['model'],
    })
