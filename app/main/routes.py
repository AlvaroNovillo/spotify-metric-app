# --- START OF (CORRECTED) FILE app/main/routes.py ---
import time
import traceback
import math
import pandas as pd
import io
from flask import (
    render_template, redirect, url_for, flash, request, current_app, session, Response
)
import spotipy

from . import main_bp
from ..spotify.auth import get_spotify_client_credentials_client
from ..spotify.data import (
    fetch_release_details, fetch_similar_artists_by_genre,
    fetch_spotify_details_for_names
)
from ..spotify.utils import calculate_release_stats
from ..lastfm.scraper import (
    scrape_all_lastfm_similar_artists_names,
    scrape_lastfm_upcoming_events,
    scrape_lastfm_tags
)

@main_bp.route('/')
def index():
    return redirect(url_for('main.search_artist'))

@main_bp.route('/search', methods=['GET'])
def search_artist():
    sp = get_spotify_client_credentials_client()
    if not sp: flash('Spotify API client could not be initialized.', 'error'); return render_template('search.html', query='', search_performed=False, main_artist=None)

    query = request.args.get('query', '').strip()
    search_performed = bool(query)
    artist_id_to_display = None
    artist_name_context = None
    main_artist_details = None
    main_artist_releases_data = []
    top_tracks_data = []
    release_stats = {}
    lastfm_events = []
    lastfm_tags = []

    if search_performed:
        print(f"Performing artist search for: '{query}'")
        last_searched = session.get('last_searched_artist', {})
        if last_searched.get('id'):
             last_id = last_searched['id']
             pool_key = f"similar_artists_pool_{last_id}"
             tags_key = f"similar_artists_tags_cache_{last_id}" # Define tags key
             session.pop(pool_key, None)
             session.pop(tags_key, None) # ***** CLEAR TAG CACHE *****
             print(f"  Cleared similar artist pool & tag cache for previous artist ID: {last_id}")
        session.pop('last_searched_artist', None)

        try:
            results = sp.search(q=query, type='artist', limit=1)
            if results and results['artists']['items']:
                artist_id_to_display = results['artists']['items'][0]['id']
                artist_name_context = results['artists']['items'][0]['name']
            else:
                flash(f'No artist found matching "{query}".', 'warning')
        except Exception as e:
            flash(f'Error during search: {e}', 'error'); traceback.print_exc()
    else:
         last_artist = session.get('last_searched_artist')
         if last_artist and isinstance(last_artist, dict) and 'id' in last_artist:
            artist_id_to_display = last_artist['id']
            artist_name_context = last_artist.get('name', 'last searched artist')
            search_performed = True


    if artist_id_to_display:
        try:
            print(f"Fetching Spotify details for artist: {artist_name_context} ({artist_id_to_display})")
            main_artist_details = sp.artist(artist_id_to_display)
            if main_artist_details:
                session['last_searched_artist'] = {'id': main_artist_details['id'], 'name': main_artist_details['name']}
                artist_name_context = main_artist_details['name']
                market = 'US'
                try:
                    # --- FIX START: Use the correct function to get top tracks with popularity ---
                    top_tracks_data = sp.artist_top_tracks(artist_id_to_display, country=market).get('tracks', [])
                    # --- FIX END ---
                except Exception as e:
                    print(f"Error fetching top tracks: {e}")
                try:
                    simplified_releases = sp.artist_albums(artist_id_to_display, album_type='album,single', limit=20).get('items', [])
                    if simplified_releases: main_artist_releases_data = fetch_release_details(sp, simplified_releases); release_stats = calculate_release_stats(main_artist_releases_data)
                except Exception as e: print(f"Error fetching releases: {e}")


                try:
                    print(f"Fetching Last.fm events for: {artist_name_context}")
                    lastfm_events_result = scrape_lastfm_upcoming_events(artist_name_context)
                    lastfm_events = lastfm_events_result if lastfm_events_result is not None else []
                    if lastfm_events_result is None: flash("Could not retrieve event data from Last.fm due to an error.", "warning")
                except Exception as event_err: print(f"Error during Last.fm event scraping call: {event_err}"); flash("An unexpected error occurred while fetching event data.", "error"); lastfm_events = []

                try:
                    print(f"Fetching Last.fm tags for: {artist_name_context}")
                    lastfm_tags_result = scrape_lastfm_tags(artist_name_context)
                    lastfm_tags = lastfm_tags_result if lastfm_tags_result is not None else []
                    if lastfm_tags_result is None: flash("Could not retrieve tag data from Last.fm due to an error.", "warning")
                except Exception as tag_err: print(f"Error during Last.fm tag scraping call: {tag_err}"); flash("An unexpected error occurred while fetching tag data.", "error"); lastfm_tags = []


            else:
                flash(f"Could not fetch details for artist '{artist_name_context}'.", 'error')
                session.pop('last_searched_artist', None)
        except Exception as e:
            flash(f'Error fetching artist details: {e}', 'error'); traceback.print_exc()
            session.pop('last_searched_artist', None)
            main_artist_details = None

    return render_template('search.html',
                           query=query,
                           search_performed=search_performed,
                           main_artist=main_artist_details,
                           main_artist_releases=main_artist_releases_data,
                           top_tracks=top_tracks_data,
                           release_stats=release_stats,
                           lastfm_events=lastfm_events,
                           lastfm_tags=lastfm_tags)


@main_bp.route('/similar-artists/<artist_id>', methods=['GET'])
def similar_artists(artist_id):
    sp = get_spotify_client_credentials_client()
    if not sp:
        flash('Spotify API client could not be initialized.', 'error')
        return redirect(url_for('main.search_artist'))

    # Parse filter and pagination arguments from the request URL
    try:
        min_followers = request.args.get('min_followers', default=None, type=int)
        max_followers = request.args.get('max_followers', default=None, type=int)
        min_popularity = request.args.get('min_popularity', default=None, type=int)
        max_popularity = request.args.get('max_popularity', default=None, type=int)
        page = request.args.get('page', default=1, type=int)
        if page < 1: page = 1
    except ValueError:
        flash("Invalid filter or page value.", "warning")
        min_followers = max_followers = min_popularity = max_popularity = None
        page = 1

    per_page = 24
    source_artist = None

    # --- MODIFICATION: Data is fetched fresh on every request to prevent session-related crashes ---
    try:
        source_artist = sp.artist(artist_id)
        source_artist_name = source_artist.get('name', 'Selected Artist')
        source_artist_genres = source_artist.get('genres', [])
        
        print(f"Fetching similar artists pool for '{source_artist_name}'...")
        spotify_genre_artists = fetch_similar_artists_by_genre(sp, artist_id, source_artist_name, source_artist_genres)
        lastfm_names = scrape_all_lastfm_similar_artists_names(source_artist_name, max_pages=5)
        lastfm_spotify_artists = fetch_spotify_details_for_names(sp, lastfm_names or [])
        
        combined_artists_map = {artist['id']: artist for artist in spotify_genre_artists if artist and artist.get('id') != artist_id}
        for artist in lastfm_spotify_artists:
            if artist and artist.get('id') != artist_id:
                combined_artists_map[artist['id']] = artist
        combined_pool = list(combined_artists_map.values())
        print(f" -> Total unique similar artist pool size: {len(combined_pool)}")
        
        # Aggregate genres from the fresh pool
        total_pool_size = len(combined_pool)
        aggregated_genres = {}
        for artist in combined_pool:
            for genre in artist.get('genres', []):
                genre_key = genre.lower()
                aggregated_genres[genre_key] = aggregated_genres.get(genre_key, {'display_name': genre, 'count': 0})
                aggregated_genres[genre_key]['count'] += 1
        sorted_genres = sorted(aggregated_genres.values(), key=lambda item: item['count'], reverse=True)

        # Apply display filters to the fresh pool
        filtered_artists = []
        for artist in combined_pool:
            followers = artist.get('followers', {}).get('total')
            popularity = artist.get('popularity')
            if min_followers is not None and (followers is None or followers < min_followers): continue
            if max_followers is not None and (followers is None or followers > max_followers): continue
            if min_popularity is not None and (popularity is None or popularity < min_popularity): continue
            if max_popularity is not None and (popularity is None or popularity > max_popularity): continue
            filtered_artists.append(artist)

        # Sort and paginate the filtered results
        filtered_artists.sort(key=lambda a: a.get('popularity', 0), reverse=True)
        total_artists = len(filtered_artists)
        total_pages = math.ceil(total_artists / per_page)
        offset = (page - 1) * per_page
        artists_on_page = filtered_artists[offset : offset + per_page]

        return render_template('similar_artists.html',
                               artists=artists_on_page, source_artist_name=source_artist_name,
                               source_artist_id=artist_id, search_genres=source_artist_genres[:3],
                               min_followers=min_followers, max_followers=max_followers,
                               min_popularity=min_popularity, max_popularity=max_popularity,
                               current_page=page, total_pages=total_pages,
                               total_artists=total_artists, per_page=per_page,
                               aggregated_genres=sorted_genres, total_pool_size=total_pool_size)

    except Exception as e:
        print(f"Unexpected error in similar_artists route: {e}")
        traceback.print_exc()
        flash('An unexpected error occurred while finding similar artists.', 'error')
        source_name = source_artist.get('name', 'the artist') if source_artist else 'the artist'
        return render_template('similar_artists.html', artists=[], source_artist_name=source_name, source_artist_id=artist_id, aggregated_genres=[], total_pool_size=0)


@main_bp.route('/download-similar/<artist_id>')
def download_similar_artists(artist_id):
    sp = get_spotify_client_credentials_client()
    if not sp:
        return "Spotify API client could not be initialized.", 500

    try:
        # --- MODIFICATION: Fetch data directly instead of relying on session ---
        source_artist = sp.artist(artist_id)
        source_artist_name = source_artist.get('name', 'artist').replace(" ", "_")
        source_artist_genres = source_artist.get('genres', [])

        print(f"Fetching artist pool for download for '{source_artist_name}'...")
        spotify_genre_artists = fetch_similar_artists_by_genre(sp, artist_id, source_artist_name, source_artist_genres)
        lastfm_names = scrape_all_lastfm_similar_artists_names(source_artist_name, max_pages=5)
        lastfm_spotify_artists = fetch_spotify_details_for_names(sp, lastfm_names or [])
        
        combined_artists_map = {artist['id']: artist for artist in spotify_genre_artists if artist and artist.get('id') != artist_id}
        for artist in lastfm_spotify_artists:
            if artist and artist.get('id') != artist_id:
                combined_artists_map[artist['id']] = artist
        combined_pool = list(combined_artists_map.values())
        print(f" -> Found {len(combined_pool)} unique artists for the download pool.")
        
        # --- Filtering logic remains the same, but now operates on the fresh data ---
        min_followers = request.args.get('min_followers', default=None, type=int)
        max_followers = request.args.get('max_followers', default=None, type=int)
        min_popularity = request.args.get('min_popularity', default=None, type=int)
        max_popularity = request.args.get('max_popularity', default=None, type=int)
        columns = request.args.get('columns', 'name,followers,popularity').split(',')
        file_format = request.args.get('format', 'csv')

        filtered_artists = []
        for artist in combined_pool:
            followers = artist.get('followers', {}).get('total')
            popularity = artist.get('popularity')
            if min_followers is not None and (followers is None or followers < min_followers): continue
            if max_followers is not None and (followers is None or followers > max_followers): continue
            if min_popularity is not None and (popularity is None or popularity < min_popularity): continue
            if max_popularity is not None and (popularity is None or popularity > max_popularity): continue
            filtered_artists.append(artist)

        if not filtered_artists:
            return "No artists match the specified filters.", 404

        df = pd.DataFrame(filtered_artists)

        column_mappers = {
            'name': lambda r: r.get('name', 'N/A'),
            'followers': lambda r: r.get('followers', {}).get('total'),
            'popularity': lambda r: r.get('popularity'),
            'genres': lambda r: ', '.join(r.get('genres', [])),
            'url': lambda r: r.get('external_urls', {}).get('spotify'),
            'image_url': lambda r: r['images'][0]['url'] if r.get('images') else None
        }

        output_df = pd.DataFrame()
        for col in columns:
            if col in column_mappers:
                output_df[col] = df.apply(column_mappers[col], axis=1)

        output = io.BytesIO()
        filename = f"similar_to_{source_artist_name}.{file_format}"

        if file_format == 'xlsx':
            output_df.to_excel(output, index=False, sheet_name='Similar Artists')
            mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        else:
            output_df.to_csv(output, index=False, encoding='utf-8')
            mimetype = 'text/csv'
        
        output.seek(0)

        return Response(
            output,
            mimetype=mimetype,
            headers={"Content-Disposition": f"attachment;filename={filename}"}
        )

    except Exception as e:
        print(f"Error during file download generation: {e}")
        traceback.print_exc()
        return "An unexpected error occurred while generating the file.", 500

# --- END OF (CORRECTED) FILE app/main/routes.py ---