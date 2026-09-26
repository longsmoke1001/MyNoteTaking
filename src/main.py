import os
import sys
# DON'T CHANGE THIS !!!
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
from flask import Flask, send_from_directory
from flask_cors import CORS
from src import database as database_config
from src.models.user import db
from src.routes.user import user_bp
from src.routes.note import note_bp
from src.routes.translate import translate_bp
from src.models.note import Note

ROOT_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))

# Load AI credentials (AI_API_KEY / AI_MODEL) and any DATABASE_URL before
# anything reads them.
load_dotenv(os.path.join(ROOT_DIR, '.env'))

app = Flask(__name__, static_folder=os.path.join(os.path.dirname(__file__), 'static'))
app.config['SECRET_KEY'] = 'asdf#FGSgvasgf$5$WGT'

# Enable CORS for all routes
CORS(app)

# register blueprints
app.register_blueprint(user_bp, url_prefix='/api')
app.register_blueprint(note_bp, url_prefix='/api')
app.register_blueprint(translate_bp, url_prefix='/api')
# Notes and users live in a local SQLite file by default, or in a hosted
# PostgreSQL database (Neon, Supabase) when DATABASE_URL is set. A serverless
# host such as Vercel needs the hosted option, because its filesystem is
# read-only and nothing persists between invocations.
#
# The database is set up on a best-effort basis and a failure is logged rather
# than raised. That keeps the app importable everywhere, so the translation API
# still works even where no database can be reached.
DB = database_config.resolve(ROOT_DIR)
app.config['SQLALCHEMY_DATABASE_URI'] = DB['uri']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = database_config.engine_options(DB)
db.init_app(app)

try:
    if DB['needs_directory']:
        # only a local SQLite file needs its directory created first
        db_path = database_config.sqlite_path(DB)
        os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)
    if DB['auto_create']:
        with app.app_context():
            db.create_all()
except Exception as exc:  # noqa: BLE001 - never block startup on storage
    app.logger.warning(
        'Database is unavailable (%s: %s). Note routes will not work, but '
        'translation does not need a database.', DB['scheme'], exc
    )

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    static_folder_path = app.static_folder
    if static_folder_path is None:
            return "Static folder not configured", 404

    if path != "" and os.path.exists(os.path.join(static_folder_path, path)):
        return send_from_directory(static_folder_path, path)
    else:
        index_path = os.path.join(static_folder_path, 'index.html')
        if os.path.exists(index_path):
            return send_from_directory(static_folder_path, 'index.html')
        else:
            return "index.html not found", 404


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
