import os
from app import create_app

# Load environment variables here if you prefer, or rely on app factory
# from dotenv import load_dotenv
# load_dotenv()

app = create_app()

if __name__ == '__main__':
    # Ensure session directory exists if using filesystem
    session_dir = app.config.get('SESSION_FILE_DIR', './.flask_session/')
    os.makedirs(session_dir, exist_ok=True)
    print(f"Flask app starting...")
    # Debug should ideally be controlled by FLASK_DEBUG env var in production
    app.run(host='127.0.0.1', port=5000, debug=True)