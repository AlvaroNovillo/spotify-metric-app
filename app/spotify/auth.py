# --- START OF FILE app/spotify/auth.py ---
import os
import traceback
from flask import current_app, flash # Keep flash if needed elsewhere, though less common now
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

# --- Client Credentials Manager ---
# Use environment variables directly as configured in Config
client_id = os.environ.get('SPOTIPY_CLIENT_ID')
client_secret = os.environ.get('SPOTIPY_CLIENT_SECRET')

# Initialize the manager only if credentials exist
client_credentials_manager = None
if client_id and client_secret:
    try:
        # REMOVED cache_path argument here
        client_credentials_manager = SpotifyClientCredentials(
            client_id=client_id,
            client_secret=client_secret
        )
        print("SpotifyClientCredentials manager initialized.")
    except Exception as e:
        print(f"ERROR initializing SpotifyClientCredentials manager: {e}")
        # Keep it None if initialization fails
        client_credentials_manager = None

else:
    print("WARNING: SPOTIPY_CLIENT_ID or SPOTIPY_CLIENT_SECRET not found. Spotify API calls will fail.")

# --- Get Spotify Client (Client Credentials) ---
def get_spotify_client_credentials_client():
    """
    Gets an authenticated Spotipy client instance using Client Credentials Flow.
    Returns None if credentials are not configured or authentication fails.
    """
    if not client_credentials_manager:
        print("Error: Spotify Client Credentials Manager not initialized. Check .env file and logs.")
        # Returning None is appropriate here
        return None

    try:
        # Create the Spotipy client using the manager
        # The manager handles token fetching and caching internally (based on Spotipy's implementation)
        sp = spotipy.Spotify(client_credentials_manager=client_credentials_manager)
        print("Spotify client created using Client Credentials Manager.")
        # Optional validation removed for brevity, manager should handle auth errors
        return sp

    except spotipy.oauth2.SpotifyOauthError as oauth_error:
        print(f"Spotify OAuth (Client Credentials) Error during client creation/token fetch: {oauth_error}")
        # Don't usually flash here, return None and let caller handle
        return None
    except Exception as e:
        print(f"Unexpected error creating Spotify client (Client Credentials): {e}")
        traceback.print_exc()
        # Don't usually flash here
        return None

# --- END OF FILE app/spotify/auth.py ---