import os
import time
from flask import Flask
from flask_session import Session
import google.generativeai as genai
from .config import Config

# Initialize extensions but don't configure them here yet
session_ext = Session()

def create_app(config_class=Config):
    """Application Factory Pattern"""
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize Flask extensions
    session_ext.init_app(app)

    # Configure Gemini
    if app.config['GEMINI_API_KEY']:
        try:
            genai.configure(api_key=app.config['GEMINI_API_KEY'])
            print("Gemini API Key configured.")
        except Exception as e:
            print(f"Warning: Failed to configure Gemini: {e}")
    else:
        print("Warning: GEMINI_API_KEY not found in environment variables.")

    # Register Blueprints
    from .main import main_bp
    app.register_blueprint(main_bp)

    from .playlists import playlists_bp
    app.register_blueprint(playlists_bp) # No URL prefix needed based on original routes

    # Example: Simple route to test app creation
    # @app.route('/hello')
    # def hello():
    #     return 'Hello, World!'

    return app