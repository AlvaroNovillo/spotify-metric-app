import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY') or 'you-will-never-guess'

    # Session Configuration
    SESSION_TYPE = os.environ.get('SESSION_TYPE') or 'filesystem'
    SESSION_FILE_DIR = os.environ.get('SESSION_FILE_DIR') or './.flask_session/'
    SESSION_PERMANENT = False
    SESSION_USE_SIGNER = True
    # Consider adding SESSION_COOKIE_SECURE=True in production if using HTTPS

    # Spotify API Credentials
    SPOTIPY_CLIENT_ID = os.environ.get('SPOTIPY_CLIENT_ID')
    SPOTIPY_CLIENT_SECRET = os.environ.get('SPOTIPY_CLIENT_SECRET')
    SPOTIPY_REDIRECT_URI = os.environ.get('SPOTIPY_REDIRECT_URI')
    SPOTIPY_CACHE_PATH = os.environ.get('SPOTIPY_CACHE_PATH') or ".spotifycache"
    SPOTIFY_SCOPE = 'user-read-private user-read-email user-top-read user-follow-read'

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

    # Application specific settings (optional)
    # e.g., DEFAULT_ARTIST_LIMIT = 20