import os
import time
from flask import Flask
from flask_session import Session
from .config import Config

# Initialize extensions but don't configure them here yet
session_ext = Session()

def create_app(config_class=Config):
    """Application Factory Pattern"""
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize Flask extensions
    session_ext.init_app(app)

    import google.generativeai as genai
    if app.config.get('GEMINI_API_KEY'):
        genai.configure(api_key=app.config['GEMINI_API_KEY'])
        print(f"Gemini configured: {app.config.get('GEMINI_MODEL_NAME')}")
    else:
        print("Warning: GEMINI_API_KEY not set.")

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