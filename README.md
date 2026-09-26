# NoteTaker - Personal Note Management Application

A modern, responsive web application for managing personal notes with a beautiful user interface and full CRUD functionality.

## 🌟 Features

- **Create Notes**: Add new notes with titles and rich content
- **Edit Notes**: Update existing notes with real-time editing
- **Delete Notes**: Remove notes you no longer need
- **Search Notes**: Find notes quickly by searching titles and content
- **Translate Notes**: Translate any note into 33 languages with a read-only preview
- **Auto-save**: Notes are automatically saved as you type
- **Responsive Design**: Works perfectly on desktop and mobile devices
- **Modern UI**: Beautiful gradient design with smooth animations
- **Real-time Updates**: Instant feedback and updates

## 🚀 Live Demo

The application is deployed and accessible at: **https://3dhkilc88dkk.manus.space**

## 🛠 Technology Stack

### Frontend
- **HTML5**: Semantic markup structure
- **CSS3**: Modern styling with gradients, animations, and responsive design
- **JavaScript (ES6+)**: Interactive functionality and API communication

### Backend
- **Python Flask**: Web framework for API endpoints
- **SQLAlchemy**: ORM for database operations
- **Flask-CORS**: Cross-origin resource sharing support
- **Requests**: HTTP client for the translation API
- **python-dotenv**: Loads the AI credentials from `.env`

### Database
- **SQLite**: Lightweight, file-based database for data persistence

## 📁 Project Structure

```
notetaking-app/
├── src/
│   ├── models/
│   │   ├── user.py          # User model (template)
│   │   └── note.py          # Note model with database schema
│   ├── routes/
│   │   ├── user.py          # User API routes (template)
│   │   ├── note.py          # Note API endpoints
│   │   └── translate.py     # Translation API endpoints
│   ├── services/
│   │   └── translator.py    # Translation service (OpenRouter / OpenAI-compatible)
│   ├── static/
│   │   ├── index.html       # Frontend application
│   │   └── favicon.ico      # Application icon
│   ├── database/
│   │   └── app.db           # SQLite database file
│   └── main.py              # Flask application entry point
├── tests/                   # pytest suite (no network access required)
├── venv/                    # Python virtual environment
├── requirements.txt         # Python dependencies
├── requirements-dev.txt     # Test dependencies
└── README.md               # This file
```

## 🔧 Local Development Setup

### Prerequisites
- Python 3.11+
- pip (Python package manager)

### Installation Steps

1. **Clone or download the project**
   ```bash
   python -m venv venv
   ```

2. **Activate the virtual environment**
   ```bash
   source venv/bin/activate
   ```

   Remark: On Windows, use `venv\Scripts\activate`

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the application**
   ```bash
   python src/main.py
   ```

5. **Access the application**
   - Open your browser and go to `http://localhost:5001`

## 🌍 Translation Setup

Translation runs through an OpenAI-compatible chat completions endpoint
(OpenRouter by default) using the credentials in a `.env` file at the project
root. That file is git-ignored, so your key stays out of version control.

```bash
AI_API_KEY=sk-or-v1-...
AI_MODEL=qwen/qwen3.8-27b:free
```

Pick any model that is available to your account. The UI picks up the setting on
startup and shows the active model next to every translation.

**Translations are a read-only preview.** Nothing is written to the database, and
the preview panel is deliberately kept separate from the title and content inputs
so the debounced auto-save can never overwrite a note with its own translation.

### Rate limits

Free-tier models are rate limited frequently. When that happens the service
retries four times with an exponential backoff (roughly 45 seconds in total)
before reporting a clear error naming the model and the `AI_MODEL` setting. Swap
in a different model in `.env` if yours stays unavailable.

Long notes are split on line boundaries so paragraphs and lists survive
translation, and hard-wrapped text is re-flowed first so the model does not copy
the original column wrapping into its output. Responses that come back truncated
are retried automatically with a larger token budget.
## 🧪 Running the Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite covers the translation endpoints, error mapping, language validation,
text chunking and response cleanup. It uses a temporary database and a mock
translator, so it never touches the network or your real notes.

## 📡 API Endpoints

### Notes API
- `GET /api/notes` - Get all notes
- `POST /api/notes` - Create a new note
- `GET /api/notes/<id>` - Get a specific note
- `PUT /api/notes/<id>` - Update a note
- `DELETE /api/notes/<id>` - Delete a note
- `GET /api/notes/search?q=<query>` - Search notes

### Translation API
- `GET /api/languages` - Supported languages, active model and setup status
- `POST /api/translate` - Translate a snippet of text
- `POST /api/notes/<id>/translate` - Translate a whole note (preview only, never saved)

### Translation Request/Response Format
A note's title and body are translated in a **single request**, so a rate limited
model only has to answer once. `title` is optional.
```json
{
  "title": "Q3 Planning",
  "text": "Deploy on Friday.",
  "target_lang": "es"
}
```
```json
{
  "translated_title": "Planificación del Q3",
  "translated_text": "Despliegue el viernes.",
  "source_lang": "auto",
  "target_lang": "es",
  "model": "qwen/qwen3.8-27b:free"
}
```

Failures return a readable message plus a machine readable code:
```json
{
  "error": "The model \"...\" is rate limited by the translation service. Try again in a minute.",
  "code": "rate_limited"
}
```

| Code | Status | Meaning |
|------|--------|---------|
| `missing_text` | 400 | No text was supplied |
| `unsupported_language` | 400 | Unknown language code |
| `text_too_long` | 413 | Input exceeds 20,000 characters |
| `not_configured` | 503 | `AI_API_KEY` is missing from `.env` |
| `rate_limited` | 429 | The model is rate limited, retries were exhausted |
| `network_error` / `upstream_error` | 502 | The translation service is unreachable or failing |

### Request/Response Format
```json
{
  "id": 1,
  "title": "My Note Title",
  "content": "Note content here...",
  "created_at": "2025-09-03T11:26:38.123456",
  "updated_at": "2025-09-03T11:27:30.654321"
}
```

## 🎨 User Interface Features

### Sidebar
- **Search Box**: Real-time search through note titles and content
- **New Note Button**: Create new notes instantly
- **Notes List**: Scrollable list of all notes with previews
- **Note Previews**: Show title, content preview, and last modified date

### Editor Panel
- **Title Input**: Edit note titles
- **Content Textarea**: Rich text editing area
- **Language Selector**: Pick any of 33 target languages (remembered between sessions)
- **Translate Button**: Produces a read-only translation preview with copy support
- **Save Button**: Manual save option (auto-save also available)
- **Delete Button**: Remove notes with confirmation
- **Real-time Updates**: Changes reflected immediately

### Design Elements
- **Gradient Background**: Beautiful purple gradient backdrop
- **Glass Morphism**: Semi-transparent panels with backdrop blur
- **Smooth Animations**: Hover effects and transitions
- **Responsive Layout**: Adapts to different screen sizes
- **Modern Typography**: Clean, readable font stack

## 🔒 Database Schema

### Notes Table
```sql
CREATE TABLE note (
    id INTEGER PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    content TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

## 🚀 Deployment

The application is configured for easy deployment with:
- CORS enabled for cross-origin requests
- Host binding to `0.0.0.0` for external access
- Production-ready Flask configuration
- Persistent SQLite database

## 🔧 Configuration

### Environment Variables
- `AI_API_KEY`: API key for the translation service (required for translation)
- `AI_MODEL`: Model id to translate with, for example `qwen/qwen3.8-27b:free`
- `AI_BASE_URL`: Override the API base URL (defaults to `https://openrouter.ai/api/v1`)
- `AI_APP_TITLE` / `AI_HTTP_REFERER`: Optional headers sent to the provider
- `FLASK_ENV`: Set to `development` for debug mode
- `SECRET_KEY`: Flask secret key for sessions
- `DATABASE_PATH`: Override the SQLite file location (defaults to `database/app.db`)

### Database Configuration
- Database file: `src/database/app.db`
- Automatic table creation on first run
- SQLAlchemy ORM for database operations

## 📱 Browser Compatibility

- Chrome/Chromium (recommended)
- Firefox
- Safari
- Edge
- Mobile browsers (iOS Safari, Chrome Mobile)

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## 📄 License

This project is open source and available under the MIT License.

## 🆘 Support

For issues or questions:
1. Check the browser console for error messages
2. Verify the Flask server is running
3. Ensure all dependencies are installed
4. Check network connectivity for the deployed version

## 🎯 Future Enhancements

Potential improvements for future versions:
- User authentication and multi-user support
- Note categories and tags
- Rich text formatting (bold, italic, lists)
- File attachments
- Export functionality (PDF, Markdown)
- Dark/light theme toggle
- Offline support with service workers
- Note sharing capabilities

---

**Built with ❤️ using Flask, SQLite, and modern web technologies**

