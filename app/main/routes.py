# --- START OF FILE app/main/routes.py ---
import time
import traceback
from flask import (
    render_template, redirect, url_for, flash, request, current_app, session # Import session
)
import spotipy

from . import main_bp
from ..spotify.auth import get_spotify_client_credentials_client
from ..spotify.data import fetch_release_details, fetch_similar_genre_artists_data
from ..spotify.utils import calculate_release_stats


# --- Core Routes ---

@main_bp.route('/')
def index():
    return redirect(url_for('main.search_artist'))

# --- Artist/Data Display Routes ---

@main_bp.route('/search', methods=['GET'])
def search_artist():
    sp = get_spotify_client_credentials_client()
    if not sp:
        flash('Spotify API client could not be initialized. Check application configuration.', 'error')
        return render_template('search.html', query='', search_performed=False, main_artist=None, main_artist_releases=[], top_tracks=[], release_stats={})

    query = request.args.get('query', '').strip()
    search_performed = bool(query)
    artist_id_to_display = None
    artist_name_context = None # For potential display if fetching by ID fails

    main_artist_details = None
    main_artist_releases_data = []
    top_tracks_data = []
    release_stats = {}

    # --- Logic to Determine Which Artist to Display ---
    if search_performed:
        # User initiated a new search
        print(f"Performing artist search for: '{query}'")
        try:
            results = sp.search(q=query, type='artist', limit=1)
            if results and results['artists']['items']:
                artist_id_to_display = results['artists']['items'][0]['id']
                artist_name_context = results['artists']['items'][0]['name'] # Get name from search result
                print(f"Search found artist: {artist_name_context} ({artist_id_to_display})")
                # Clear previous session data if a new search is successful
                session.pop('last_searched_artist', None)
            else:
                print(f"No artist found matching '{query}'.")
                flash(f'No artist found matching "{query}". Please try a different name.', 'warning')
                session.pop('last_searched_artist', None) # Clear session on failed search too
        except spotipy.exceptions.SpotifyException as e:
            print(f"Spotify API error during search: {e}")
            flash(f'A Spotify error occurred during the search: {e.msg}.', 'error')
            session.pop('last_searched_artist', None)
        except Exception as e:
            print(f"Unexpected error during artist search: {e}")
            flash('An unexpected error occurred during the search.', 'error')
            traceback.print_exc()
            session.pop('last_searched_artist', None)

    else:
        # No new search query, check session for last searched artist
        last_artist = session.get('last_searched_artist')
        if last_artist and isinstance(last_artist, dict) and 'id' in last_artist:
            artist_id_to_display = last_artist['id']
            artist_name_context = last_artist.get('name', 'last searched artist') # Use stored name
            print(f"Loading last searched artist from session: {artist_name_context} ({artist_id_to_display})")
            search_performed = True # Treat loading from session as having performed a search for display purposes
            # Keep query empty in the form unless we want to prefill it? For now, keep it empty.

    # --- Fetch and Display Artist Details (if an ID was determined) ---
    if artist_id_to_display:
        try:
            print(f"Fetching details for artist ID: {artist_id_to_display}...")
            main_artist_details = sp.artist(artist_id_to_display)

            if main_artist_details:
                # Successfully fetched, STORE/UPDATE in session
                session['last_searched_artist'] = {
                    'id': main_artist_details['id'],
                    'name': main_artist_details['name']
                }
                artist_name_context = main_artist_details['name'] # Ensure context name is accurate

                # Fetch Top Tracks
                print(f"Fetching top tracks for {artist_name_context}...")
                try:
                    market = 'US'
                    top_tracks_result = sp.artist_top_tracks(artist_id_to_display, country=market)
                    if top_tracks_result and top_tracks_result.get('tracks'):
                        top_tracks_data = top_tracks_result['tracks']
                    else: print(f"  No public top tracks found in {market}.")
                except Exception as e: print(f"  Error fetching top tracks: {e}") # Log non-critically

                # Fetch Releases
                print(f"Fetching releases for {artist_name_context}...")
                try:
                    simplified_releases_result = sp.artist_albums(artist_id_to_display, album_type='album,single', limit=20)
                    if simplified_releases_result and simplified_releases_result['items']:
                        simplified_releases = simplified_releases_result['items']
                        main_artist_releases_data = fetch_release_details(sp, simplified_releases)
                        release_stats = calculate_release_stats(main_artist_releases_data)
                    else: print(f"No primary releases found for {artist_name_context}.")
                except Exception as e: print(f"  Error fetching releases: {e}") # Log non-critically

            else:
                # Failed to fetch details even with an ID (e.g., artist removed?)
                flash(f"Could not fetch details for artist '{artist_name_context}' (ID: {artist_id_to_display}).", 'error')
                session.pop('last_searched_artist', None) # Remove invalid data from session

        except spotipy.exceptions.SpotifyException as e:
            print(f"Spotify API error fetching details for ID {artist_id_to_display}: {e}")
            flash(f'A Spotify error occurred fetching details: {e.msg}.', 'error')
            session.pop('last_searched_artist', None) # Remove potentially invalid ID
            main_artist_details = None # Ensure nothing is displayed
        except Exception as e:
            print(f"Unexpected error fetching details for ID {artist_id_to_display}: {e}")
            flash('An unexpected error occurred fetching artist details.', 'error')
            traceback.print_exc()
            session.pop('last_searched_artist', None)
            main_artist_details = None

    # Render the template
    return render_template('search.html',
                           query=query, # Display the user's search query if they entered one
                           search_performed=search_performed, # Indicates if *any* artist is being displayed
                           main_artist=main_artist_details,
                           main_artist_releases=main_artist_releases_data,
                           top_tracks=top_tracks_data,
                           release_stats=release_stats)

# --- /similar-genre-artists and other routes remain unchanged ---
# They already operate based on the artist_id passed in the URL

@main_bp.route('/similar-genre-artists/<artist_id>')
def similar_genre_artists(artist_id):
    sp = get_spotify_client_credentials_client()
    if not sp:
        flash('Spotify API client could not be initialized.', 'error')
        return redirect(url_for('main.search_artist'))

    if not artist_id:
        flash('No artist ID provided for finding similar artists.', 'error')
        return redirect(url_for('main.search_artist'))

    try:
        print(f"Fetching details for artist ID {artist_id} to find similar artists...")
        source_artist = sp.artist(artist_id)
        if not source_artist:
            flash(f"Could not find details for artist ID {artist_id}.", 'error')
            return redirect(url_for('main.search_artist'))

        source_artist_name = source_artist.get('name', 'Selected Artist')
        source_artist_genres = source_artist.get('genres', [])
        print(f"Source artist: {source_artist_name}, Genres: {source_artist_genres}")

        if not source_artist_genres:
            flash(f'No genres found for {source_artist_name}. Cannot find similar artists by genre.', 'info')
            return render_template('similar_genre.html', artists=[], main_artist_name=source_artist_name, search_genres=[], source_artist_id=artist_id)

        genres_to_search = source_artist_genres[:2]
        similar_artists_data = fetch_similar_genre_artists_data(
            sp, artist_id, source_artist_name, genres_to_search, limit=24
        )

        if not similar_artists_data:
            flash(f'Could not find other artists based on the genres: {", ".join(genres_to_search)}.', 'info')

        return render_template('similar_genre.html',
                               artists=similar_artists_data,
                               main_artist_name=source_artist_name,
                               search_genres=genres_to_search,
                               source_artist_id=artist_id)

    except spotipy.exceptions.SpotifyException as e:
        print(f"Spotify API error fetching similar artists: {e}")
        flash(f'A Spotify error occurred: {e.msg}', 'error')
        source_name = source_artist.get('name', 'the selected artist') if 'source_artist' in locals() else 'the artist'
        return render_template('similar_genre.html', artists=[], main_artist_name=source_name, search_genres=[], source_artist_id=artist_id)
    except Exception as e:
        print(f"Unexpected error fetching similar artists: {e}")
        flash('An unexpected error occurred while finding similar artists.', 'error')
        traceback.print_exc()
        source_name = source_artist.get('name', 'the selected artist') if 'source_artist' in locals() else 'the artist'
        return render_template('similar_genre.html', artists=[], main_artist_name=source_name, search_genres=[], source_artist_id=artist_id)

# --- END OF FILE app/main/routes.py ---