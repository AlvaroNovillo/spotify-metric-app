# --- START OF (REVISED) FILE app/main/routes.py ---
import time
import traceback
import math # Needed for pagination calculation
from flask import (
    render_template, redirect, url_for, flash, request, current_app, session
)
import spotipy

from . import main_bp
from ..spotify.auth import get_spotify_client_credentials_client
from ..spotify.data import (
    fetch_release_details, fetch_similar_artists_by_genre,
    fetch_spotify_details_for_names
)
from ..spotify.utils import calculate_release_stats
from ..lastfm.scraper import scrape_all_lastfm_similar_artists_names

# --- Routes ---
@main_bp.route('/')
def index():
    # Clear session caches when going back to the main search? Optional.
    # session.pop('last_searched_artist', None)
    # # Clear similar artist pools if needed (use prefix search if many keys)
    # keys_to_remove = [k for k in session if k.startswith('similar_artists_pool_')]
    # for key in keys_to_remove:
    #     session.pop(key, None)
    return redirect(url_for('main.search_artist'))

@main_bp.route('/search', methods=['GET'])
def search_artist():
    # ... (search_artist route remains the same - maybe add session clear here too) ...
    sp = get_spotify_client_credentials_client()
    if not sp: flash('Spotify API client could not be initialized.', 'error'); return render_template('search.html', query='', search_performed=False, main_artist=None)
    query = request.args.get('query', '').strip(); search_performed = bool(query); artist_id_to_display = None; artist_name_context = None
    main_artist_details = None; main_artist_releases_data = []; top_tracks_data = []; release_stats = {}
    if search_performed:
        print(f"Performing artist search for: '{query}'");
        # Clear previous similar artist pool when a NEW search is done
        session.pop(f"similar_artists_pool_{session.get('last_searched_artist', {}).get('id', 'none')}", None)
        session.pop('last_searched_artist', None)
        try:
            results = sp.search(q=query, type='artist', limit=1)
            if results and results['artists']['items']: artist_id_to_display = results['artists']['items'][0]['id']; artist_name_context = results['artists']['items'][0]['name']
            else: flash(f'No artist found matching "{query}".', 'warning')
        except Exception as e: flash(f'Error during search: {e}', 'error'); traceback.print_exc()
    else:
        last_artist = session.get('last_searched_artist')
        if last_artist and isinstance(last_artist, dict) and 'id' in last_artist: artist_id_to_display = last_artist['id']; artist_name_context = last_artist.get('name', 'last searched artist'); search_performed = True
    if artist_id_to_display:
        try:
            main_artist_details = sp.artist(artist_id_to_display)
            if main_artist_details:
                session['last_searched_artist'] = {'id': main_artist_details['id'], 'name': main_artist_details['name']}; artist_name_context = main_artist_details['name']
                market = 'US'
                try: top_tracks_data = sp.artist_top_tracks(artist_id_to_display, country=market).get('tracks', [])
                except Exception as e: print(f"Error fetching top tracks: {e}")
                try:
                    simplified_releases = sp.artist_albums(artist_id_to_display, album_type='album,single', limit=20).get('items', [])
                    if simplified_releases: main_artist_releases_data = fetch_release_details(sp, simplified_releases); release_stats = calculate_release_stats(main_artist_releases_data)
                except Exception as e: print(f"Error fetching releases: {e}")
            else: flash(f"Could not fetch details for artist '{artist_name_context}'.", 'error'); session.pop('last_searched_artist', None)
        except Exception as e: flash(f'Error fetching artist details: {e}', 'error'); traceback.print_exc(); session.pop('last_searched_artist', None); main_artist_details = None
    return render_template('search.html',
                           query=query, search_performed=search_performed, main_artist=main_artist_details,
                           main_artist_releases=main_artist_releases_data, top_tracks=top_tracks_data, release_stats=release_stats)


# --- REVISED Similar Artists Route with Caching and Pagination ---
@main_bp.route('/similar-artists/<artist_id>', methods=['GET'])
def similar_artists(artist_id):
    sp = get_spotify_client_credentials_client()
    if not sp: flash('Spotify API client could not be initialized.', 'error'); return redirect(url_for('main.search_artist'))
    if not artist_id: flash('No artist ID provided.', 'error'); return redirect(url_for('main.search_artist'))

    # Get Filter & Pagination Parameters
    try:
        min_followers=request.args.get('min_followers', default=None, type=int)
        max_followers=request.args.get('max_followers', default=None, type=int)
        min_popularity=request.args.get('min_popularity', default=None, type=int)
        max_popularity=request.args.get('max_popularity', default=None, type=int)
        page = request.args.get('page', default=1, type=int)
        if page < 1: page = 1
    except ValueError:
        flash("Invalid filter or page value.", "warning")
        min_followers=max_followers=min_popularity=max_popularity=None
        page = 1

    per_page = 24 # Artists per page
    source_artist = None
    source_artist_name = "Selected Artist"
    source_artist_genres = []
    session_pool_key = f"similar_artists_pool_{artist_id}"
    combined_pool = []

    try:
        # 1. Get Source Artist Details (Always needed for context)
        print(f"Fetching source artist details for ID: {artist_id}")
        source_artist = sp.artist(artist_id)
        if not source_artist: flash(f"Could not find source artist ID {artist_id}.", 'error'); return redirect(url_for('main.search_artist'))
        source_artist_name = source_artist.get('name', 'Selected Artist')
        source_artist_genres = source_artist.get('genres', [])
        print(f"Source artist: {source_artist_name}")

        # 2. Check Session Cache for Artist Pool
        if session_pool_key in session:
            print(f"Found similar artists pool for '{source_artist_name}' in session cache.")
            combined_pool = session[session_pool_key]
            # Ensure it's a list (basic validation)
            if not isinstance(combined_pool, list):
                 print("  Warning: Session data invalid, clearing and refetching.")
                 session.pop(session_pool_key, None)
                 combined_pool = [] # Force refetch

        # 3. Fetch if not in Cache
        if not combined_pool:
            print(f"Similar artists pool for '{source_artist_name}' not cached. Fetching...")
            # 3a. Fetch Spotify Genre Artists
            print(" -> Executing Spotify genre search...")
            spotify_genre_artists = fetch_similar_artists_by_genre(
                sp, artist_id, source_artist_name, source_artist_genres
            )
            print(f" -> Found {len(spotify_genre_artists)} candidates via Spotify genres.")

            # 3b. Fetch Last.fm Names
            print(f" -> Executing Last.fm multi-page scrape for '{source_artist_name}'...")
            lastfm_names = scrape_all_lastfm_similar_artists_names(source_artist_name, max_pages=7)
            if lastfm_names is None: flash("Error connecting to Last.fm.", "warning"); lastfm_names = []
            elif not lastfm_names: flash(f"No similar artists found on '{source_artist_name}' on Last.fm.", "info")

            # 3c. Fetch Spotify Details for Last.fm Names
            lastfm_spotify_artists = []
            if lastfm_names:
                print(f" -> Looking up Spotify details for {len(lastfm_names)} Last.fm names...")
                lastfm_spotify_artists = fetch_spotify_details_for_names(sp, lastfm_names)
                if not lastfm_spotify_artists and lastfm_names: flash("Could not find Spotify details for artists found on Last.fm.", "info")

            # 3d. Merge and De-duplicate
            print(" -> Merging and de-duplicating results...")
            combined_artists_map = {}
            for artist in spotify_genre_artists:
                if artist and artist.get('id') and artist['id'] != artist_id:
                    combined_artists_map[artist['id']] = artist
            for artist in lastfm_spotify_artists:
                 if artist and artist.get('id') and artist['id'] != artist_id:
                    combined_artists_map[artist['id']] = artist
            combined_pool = list(combined_artists_map.values())
            print(f" -> Total unique similar artist pool size: {len(combined_pool)}")

            # 3e. Store in Session Cache
            try:
                session[session_pool_key] = combined_pool
                print(f" -> Stored pool in session cache (key: {session_pool_key}).")
            except Exception as cache_err:
                 print(f" -> Warning: Failed to store artist pool in session: {cache_err}")
                 flash("Warning: Could not cache results, filtering/paging might re-fetch data.", "warning")

        # --- Processing Steps (Applied to cached or freshly fetched pool) ---

        # 4. Apply Filters
        filtered_artists = []
        if combined_pool:
            print(f"Applying filters LOCALLY to pool of {len(combined_pool)}...")
            for artist in combined_pool:
                followers = artist.get('followers', {}).get('total'); popularity = artist.get('popularity')
                if min_followers is not None and (followers is None or followers < min_followers): continue
                if max_followers is not None and (followers is None or followers > max_followers): continue
                if min_popularity is not None and (popularity is None or popularity < min_popularity): continue
                if max_popularity is not None and (popularity is None or popularity > max_popularity): continue
                filtered_artists.append(artist)
            print(f"Filtered list size: {len(filtered_artists)}")
        else:
            print("Combined similar artist pool is empty.")

        # 5. Sort Final Filtered List by Popularity
        if filtered_artists:
             filtered_artists.sort(key=lambda a: a.get('popularity', 0), reverse=True)

        # 6. Paginate the Filtered List
        total_artists = len(filtered_artists)
        total_pages = math.ceil(total_artists / per_page)
        offset = (page - 1) * per_page
        artists_on_page = filtered_artists[offset : offset + per_page]
        print(f"Pagination: Page {page}/{total_pages}, showing {len(artists_on_page)}/{total_artists} artists.")

        # 7. Flash Messages
        if not artists_on_page and combined_pool:
             if min_followers or max_followers or min_popularity or max_popularity:
                 flash('No artists match the applied filters.', 'info')
             else:
                 # This case shouldn't happen if combined_pool is not empty unless page number is invalid
                 flash('No artists to display on this page.', 'info')
        elif not combined_pool:
             msg = f'Could not find any similar artists for {source_artist_name} from Spotify or Last.fm.'
             flash(msg, 'warning')

        # 8. Render Template
        return render_template('similar_artists.html',
                               artists=artists_on_page, # Pass only the artists for the current page
                               source_artist_name=source_artist_name,
                               source_artist_id=artist_id,
                               search_genres=source_artist_genres[:3],
                               # Filters for form stickiness
                               min_followers=min_followers, max_followers=max_followers,
                               min_popularity=min_popularity, max_popularity=max_popularity,
                               # Pagination data
                               current_page=page,
                               total_pages=total_pages,
                               total_artists=total_artists,
                               per_page=per_page)

    except spotipy.exceptions.SpotifyException as e:
        print(f"Spotify API error: {e}"); flash(f'Spotify error: {e.msg}', 'error')
        source_name = source_artist.get('name', 'the artist') if source_artist else 'the artist'
        session.pop(session_pool_key, None) # Clear cache on error
        return render_template('similar_artists.html', artists=[], source_artist_name=source_name, source_artist_id=artist_id, current_page=1, total_pages=0, total_artists=0)
    except Exception as e:
        print(f"Unexpected error in route: {e}"); flash('Unexpected error.', 'error'); traceback.print_exc()
        source_name = source_artist.get('name', 'the artist') if source_artist else 'the artist'
        session.pop(session_pool_key, None) # Clear cache on error
        return render_template('similar_artists.html', artists=[], source_artist_name=source_name, source_artist_id=artist_id, current_page=1, total_pages=0, total_artists=0)

# --- END OF (REVISED) FILE app/main/routes.py ---