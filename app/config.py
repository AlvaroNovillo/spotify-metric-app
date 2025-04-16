import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY') or 'you-will-never-guess'

    # Session Configuration (Still potentially useful for flash messages or future features)
    SESSION_TYPE = os.environ.get('SESSION_TYPE') or 'filesystem'
    SESSION_FILE_DIR = os.environ.get('SESSION_FILE_DIR') or './.flask_session/'
    SESSION_PERMANENT = False
    SESSION_USE_SIGNER = True

    # Spotify API Credentials (Client ID & Secret are needed for Client Credentials Flow)
    SPOTIPY_CLIENT_ID = os.environ.get('SPOTIPY_CLIENT_ID')
    SPOTIPY_CLIENT_SECRET = os.environ.get('SPOTIPY_CLIENT_SECRET')
    SPOTIPY_CACHE_PATH = os.environ.get('SPOTIPY_CACHE_PATH') or ".spotifycache" # Cache path might still be used

    # Gemini API Key
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME") or "gemini-1.5-flash"

    # PlaylistSupply Credentials (USE WITH EXTREME CAUTION)
    PLAYLIST_SUPPLY_USER = os.environ.get("PLAYLIST_SUPPLY_USER")
    PLAYLIST_SUPPLY_PASS = os.environ.get("PLAYLIST_SUPPLY_PASS")

    # Email Sender Credentials (for SMTP)
    SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
    SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD") # Use App Password for Gmail
    SMTP_SERVER = os.environ.get("SMTP_SERVER") or "smtp.gmail.com"
    SMTP_PORT = int(os.environ.get("SMTP_PORT") or 587)