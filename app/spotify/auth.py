# --- START OF FILE app/spotify/auth.py ---
import os
import time
from flask import session, redirect, url_for, flash, current_app
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# Initialize OAuth object using config from app factory
# This assumes create_app sets up the config before this module is heavily used.
# A more robust approach might pass the app instance or config explicitly if needed outside request context.
sp_oauth = SpotifyOAuth(
    client_id=os.environ.get('SPOTIPY_CLIENT_ID'), # Direct os.environ access needed at import time
    client_secret=os.environ.get('SPOTIPY_CLIENT_SECRET'),
    redirect_uri=os.environ.get('SPOTIPY_REDIRECT_URI'),
    scope=os.environ.get('SPOTIFY_SCOPE', 'user-read-private user-read-email user-top-read user-follow-read'),
    cache_path=os.environ.get('SPOTIPY_CACHE_PATH', ".spotifycache")
)


def get_token_info():
    """Retrieves token info from session or cache, handling potential refresh."""
    token_info = session.get('token_info', None)

    if not token_info:
        # Fallback to cache if not in session (e.g., after server restart)
        cached_token = sp_oauth.get_cached_token()
        if cached_token:
            token_info = cached_token
            session['token_info'] = token_info # Store in session if found in cache
            print("Token info loaded from cache into session.")
        else:
             # No token in session or cache
             return None

    # Check if token is expired or nearing expiration (e.g., within 60 seconds)
    now = int(time.time())
    is_expired = token_info.get('expires_at', 0) - now < 60

    if is_expired:
        print("Spotify token expired or nearing expiration, attempting refresh...")
        try:
            refresh_token = token_info.get('refresh_token')
            if not refresh_token:
                print("No refresh token found in token info. Cannot refresh.")
                session.clear() # Clear session as token is invalid
                return None

            # Attempt to refresh the token
            token_info = sp_oauth.refresh_access_token(refresh_token)
            session['token_info'] = token_info # IMPORTANT: Update session with new token
            print("Spotify token successfully refreshed.")

        except spotipy.exceptions.SpotifyOauthError as e:
            print(f"Spotify OAuth error during token refresh: {e}")
            flash('Your Spotify login session has expired. Please login again.', 'error')
            session.clear() # Clear session on refresh failure
            return None
        except Exception as e:
            print(f"Unexpected error refreshing Spotify token: {e}")
            flash('An error occurred while refreshing your Spotify session. Please login again.', 'error')
            session.clear() # Clear session on unexpected error
            return None

    return token_info

def get_spotify_client():
    """Gets an authenticated Spotipy client instance, handling token refresh."""
    token_info = get_token_info()

    if not token_info:
        print("No valid Spotify token info found.")
        # Don't flash here, let the calling route handle UI messages if needed
        return None

    try:
        # Create the Spotipy client with the valid access token
        sp = spotipy.Spotify(auth=token_info['access_token'])
        # Optional: Verify client works with a simple call
        # sp.current_user()
        return sp
    except Exception as e:
        print(f"Error creating Spotify client instance: {e}")
        return None

# --- END OF FILE app/spotify/auth.py ---