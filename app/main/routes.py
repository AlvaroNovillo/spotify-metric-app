import os
import time
import traceback
from flask import (
    render_template, redirect, url_for, session, flash, request, current_app
)
import spotipy

from . import main_bp
from ..spotify.auth import get_spotify_client, sp_oauth # Import auth functions/objects
from ..spotify.data import fetch_release_details, fetch_similar_genre_artists_data # Import data fetching
from ..spotify.utils import calculate_release_stats # Import utils


# --- Core Routes ---

@main_bp.route('/')
def index():
    # Pass token info to template to conditionally show login/dashboard links
    token_info = session.get('token_info')
    return render_template('home.html', token_info=token_info)

@main_bp.route('/login')
def login():
    # Clear previous cache and session
    try:
        cache_path = current_app.config['SPOTIPY_CACHE_PATH']
        if os.path.exists(cache_path):
            os.remove(cache_path)
            print(f"Removed cache file: {cache_path}")
    except Exception as e:
        print(f"Error removing cache file: {e}")
    session.clear()

    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)

@main_bp.route('/callback')
def callback():
    session.clear() # Ensure clean slate before attempting login
    try:
        code = request.args.get('code')
        if not code:
            raise ValueError("Authorization code missing from callback.")

        # Get token, ensuring cache is NOT used for this specific step
        token_info = sp_oauth.get_access_token(code, check_cache=False)

        if not token_info:
            raise ValueError("Failed to retrieve token info from Spotify.")

        # Basic validation of token structure
        required_keys = ['access_token', 'refresh_token', 'expires_at', 'scope']
        if not all(key in token_info for key in required_keys):
            raise ValueError("Incomplete token information received.")

        session['token_info'] = token_info
        flash('Login successful!', 'success')
        # Redirect to a meaningful page, e.g., artist dashboard
        return redirect(url_for('main.artist_dashboard'))

    except Exception as e:
        print(f"Callback Error: {e}")
        traceback.print_exc()
        flash(f'Authentication failed: {str(e)}. Please try logging in again.', 'error')
        # Redirect back to the home page on failure
        return redirect(url_for('main.index'))

@main_bp.route('/logout')
def logout():
    session.clear()
    try:
        cache_path = current_app.config['SPOTIPY_CACHE_PATH']
        if os.path.exists(cache_path):
            os.remove(cache_path)
            print(f"Removed cache file: {cache_path}")
    except Exception as e:
        print(f"Error removing cache file: {e}")
    flash('You have been logged out.', 'info')
    return redirect(url_for('main.index'))

# --- Artist/Data Display Routes ---

@main_bp.route('/artist')
def artist_dashboard():
    sp = get_spotify_client()
    if not sp:
        flash('Your Spotify session is invalid or expired. Please login again.', 'error')
        return redirect(url_for('main.login'))

    artist_data = None
    releases_data = []
    top_tracks_data = []
    release_stats = {}
    playlists_mentioning = [] # Placeholder if you add this feature later

    try:
        print("Attempting to fetch user's top or followed artist...")
        # Try fetching top artist first
        top_artists = sp.current_user_top_artists(limit=1, time_range='long_term')
        artist_id = None
        artist_name = None

        if top_artists and top_artists['items']:
            artist_id = top_artists['items'][0]['id']
            artist_name = top_artists['items'][0]['name']
            print(f"Found top artist: {artist_name} ({artist_id})")
        else:
            # Fallback to most recently followed artist
            print("No top artist found, checking followed artists...")
            followed_artists = sp.current_user_followed_artists(limit=1)
            if followed_artists and followed_artists['artists']['items']:
                artist_id = followed_artists['artists']['items'][0]['id']
                artist_name = followed_artists['artists']['items'][0]['name']
                print(f"Found followed artist: {artist_name} ({artist_id})")
                flash('Displaying data for your most recently followed artist.', 'info')

        if artist_id:
            print(f"Fetching full details for artist ID: {artist_id}...")
            artist_data = sp.artist(artist_id)

            if artist_data:
                # Store essential info in session for other routes
                session['main_artist_id'] = artist_data['id']
                session['main_artist_name'] = artist_data['name']
                session['main_artist_genres'] = artist_data.get('genres', [])
                print(f"Successfully fetched data for: {artist_data['name']}.")

                # Fetch Top Tracks
                print(f"Fetching top tracks for {artist_data['name']}...")
                try:
                    user_info = sp.current_user()
                    market = user_info.get('country', 'US') # Use user's market
                    top_tracks_result = sp.artist_top_tracks(artist_data['id'], country=market)
                    if top_tracks_result and top_tracks_result.get('tracks'):
                        top_tracks_data = top_tracks_result['tracks']
                        print(f"  Found {len(top_tracks_data)} top tracks.")
                    else:
                        print("  No public top tracks found or returned by API.")
                        flash('No public top tracks found for this artist in your region.', 'info')
                except spotipy.exceptions.SpotifyException as e:
                    print(f"  Spotify API error fetching top tracks: {e}")
                    flash(f'Could not fetch top tracks: {e.msg}', 'warning')
                except Exception as e:
                    print(f"  Unexpected error fetching top tracks: {e}")
                    flash('An unexpected error occurred while fetching top tracks.', 'warning')
                    traceback.print_exc()

                # Fetch Releases (Simplified List First)
                print(f"Fetching simplified releases list for {artist_data['name']}...")
                simplified_releases = []
                try:
                    simplified_releases_result = sp.artist_albums(artist_data['id'], album_type='album,single', limit=20) # Limit initial pull
                    if simplified_releases_result and simplified_releases_result['items']:
                        simplified_releases = simplified_releases_result['items']
                        print(f"Found {len(simplified_releases)} simplified releases. Fetching full details...")
                        # Fetch Full Details (uses helper from data.py)
                        releases_data = fetch_release_details(sp, simplified_releases) # Pass sp client
                        # Calculate Stats (uses helper from utils.py)
                        release_stats = calculate_release_stats(releases_data)
                    else:
                        print("No primary releases found for this artist.")
                        flash('No recent albums or singles found for this artist.', 'info')
                except spotipy.exceptions.SpotifyException as e:
                    print(f"  Spotify API error fetching releases: {e}")
                    flash(f'Could not fetch releases: {e.msg}', 'warning')
                except Exception as e:
                    print(f"  Unexpected error fetching releases: {e}")
                    flash('An unexpected error occurred while fetching releases.', 'warning')
                    traceback.print_exc()

            else:
                # This case should be rare if artist_id was valid
                print(f"Could not fetch full details for artist ID: {artist_id}")
                flash('Could not fetch artist details even though an ID was found.', 'warning')
                # Clear potentially stale session data
                session.pop('main_artist_id', None)
                session.pop('main_artist_name', None)
                session.pop('main_artist_genres', None)

        else:
            print("No top or followed artist could be determined for the user.")
            flash('Could not find your top or most recently followed artist. Please follow an artist on Spotify and try again.', 'warning')
            # Ensure session is clear if no artist is found
            session.pop('main_artist_id', None)
            session.pop('main_artist_name', None)
            session.pop('main_artist_genres', None)

        return render_template('artist.html',
                               artist=artist_data,
                               releases=releases_data,
                               top_tracks=top_tracks_data,
                               release_stats=release_stats,
                               playlists_mentioning=playlists_mentioning) # Pass playlist data if available

    except spotipy.exceptions.SpotifyException as e:
        print(f"Spotify API error occurred in artist_dashboard: {e}")
        traceback.print_exc()
        if e.http_status == 401:
            flash('Your Spotify session has expired. Please login again.', 'error')
            session.clear()
            return redirect(url_for('main.login'))
        elif e.http_status == 403:
            flash('Permissions error. Please ensure you granted the necessary permissions during login.', 'error')
            # Optionally redirect to index or logout
            return redirect(url_for('main.index'))
        else:
            flash(f'A Spotify error occurred: {e.msg}. Please try again later.', 'error')
        # Render the template with whatever data was fetched before the error
        return render_template('artist.html',
                               artist=artist_data,
                               releases=releases_data,
                               top_tracks=top_tracks_data,
                               release_stats=release_stats,
                               playlists_mentioning=playlists_mentioning)
    except Exception as e:
        print(f"Unexpected error in artist_dashboard: {e}")
        flash('An unexpected error occurred. Please try again later.', 'error')
        traceback.print_exc()
        # Render the template with potentially partial data
        return render_template('artist.html',
                               artist=artist_data,
                               releases=releases_data,
                               top_tracks=top_tracks_data,
                               release_stats=release_stats,
                               playlists_mentioning=playlists_mentioning)


@main_bp.route('/similar-genre-artists')
def similar_genre_artists():
    sp = get_spotify_client()
    if not sp:
        flash('Session invalid. Please login.', 'error')
        return redirect(url_for('main.login'))

    # Get main artist info from session (set in artist_dashboard)
    main_artist_id = session.get('main_artist_id')
    main_artist_name = session.get('main_artist_name', 'your artist')
    main_artist_genres = session.get('main_artist_genres', [])

    if not main_artist_id:
        flash('Main artist data not found in session. Please visit the "My Artist" page first.', 'warning')
        return redirect(url_for('main.artist_dashboard'))

    if not main_artist_genres:
        flash(f'No genres found for {main_artist_name}. Cannot find similar artists by genre.', 'info')
        return render_template('similar_genre.html', artists=[], main_artist_name=main_artist_name, search_genres=[])

    # Use helper function to get data
    genres_to_search = main_artist_genres[:2] # Use top 2 genres for search
    similar_artists_data = fetch_similar_genre_artists_data(sp, main_artist_id, main_artist_name, genres_to_search, limit=24) # Pass client

    if not similar_artists_data:
        flash(f'Could not find other artists based on the genres: {", ".join(genres_to_search)}.', 'info')

    return render_template('similar_genre.html',
                           artists=similar_artists_data,
                           main_artist_name=main_artist_name,
                           search_genres=genres_to_search)


@main_bp.route('/search', methods=['GET'])
def search_artist():
    sp = get_spotify_client()
    if not sp:
        flash('Please login to search for artists.', 'info')
        return redirect(url_for('main.login'))

    query = request.args.get('query', '').strip()
    search_performed = bool(query)
    main_artist_details = None
    main_artist_releases_data = []
    top_tracks_data = []
    release_stats = {}

    if search_performed:
        print(f"Performing artist search for: '{query}'")
        try:
            results = sp.search(q=query, type='artist', limit=1)

            if results and results['artists']['items']:
                # Artist found, fetch details
                artist_id = results['artists']['items'][0]['id']
                artist_name = results['artists']['items'][0]['name']
                print(f"Found artist: {artist_name} ({artist_id}). Fetching details...")
                main_artist_details = sp.artist(artist_id)

                # Fetch Top Tracks
                print(f"Fetching top tracks for {artist_name}...")
                try:
                    # Determine market, default to US if user info fails
                    market = 'US'
                    try:
                         user_info = sp.current_user()
                         market = user_info.get('country', 'US')
                    except Exception:
                         print("Could not get user market, defaulting to US for top tracks.")

                    top_tracks_result = sp.artist_top_tracks(artist_id, country=market)
                    if top_tracks_result and top_tracks_result.get('tracks'):
                        top_tracks_data = top_tracks_result['tracks']
                        print(f"  Found {len(top_tracks_data)} top tracks.")
                    else:
                        print("  No public top tracks found.")
                except spotipy.exceptions.SpotifyException as e:
                    print(f"  Spotify API error fetching top tracks for search result: {e}")
                except Exception as e:
                    print(f"  Unexpected error fetching top tracks for search result: {e}")
                    traceback.print_exc()

                # Fetch Releases
                print(f"Fetching releases for {artist_name}...")
                simplified_releases = []
                try:
                    simplified_releases_result = sp.artist_albums(artist_id, album_type='album,single', limit=20)
                    if simplified_releases_result and simplified_releases_result['items']:
                        simplified_releases = simplified_releases_result['items']
                        print(f"Found {len(simplified_releases)} releases. Fetching details...")
                        # Fetch Full Details
                        main_artist_releases_data = fetch_release_details(sp, simplified_releases) # Pass client
                        # Calculate Stats
                        release_stats = calculate_release_stats(main_artist_releases_data)
                    else:
                        print(f"No primary releases found for {artist_name}.")
                except spotipy.exceptions.SpotifyException as e:
                    print(f"  Spotify API error fetching releases for search result: {e}")
                except Exception as e:
                    print(f"  Unexpected error fetching releases for search result: {e}")
                    traceback.print_exc()

            else:
                print(f"No artist found matching '{query}'.")
                flash(f'No artist found matching "{query}". Please try a different name.', 'warning')

        except spotipy.exceptions.SpotifyException as e:
            print(f"Spotify API error during search: {e}")
            if e.http_status == 401:
                flash('Your Spotify session has expired. Please login again.', 'error')
                session.clear()
                return redirect(url_for('main.login'))
            else:
                flash(f'A Spotify error occurred during the search: {e.msg}', 'error')
            # Reset data on error
            main_artist_details = None; main_artist_releases_data = []; top_tracks_data = []; release_stats = {}
        except Exception as e:
            print(f"Unexpected error during artist search: {e}")
            flash('An unexpected error occurred during the search.', 'error')
            traceback.print_exc()
            # Reset data on error
            main_artist_details = None; main_artist_releases_data = []; top_tracks_data = []; release_stats = {}

    return render_template('search.html',
                           query=query,
                           search_performed=search_performed,
                           main_artist=main_artist_details,
                           main_artist_releases=main_artist_releases_data,
                           top_tracks=top_tracks_data,
                           release_stats=release_stats)