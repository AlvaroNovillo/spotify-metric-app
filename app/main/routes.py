import time
import traceback
import math
import pandas as pd
import io
from flask import (
    render_template, redirect, url_for, flash, request, current_app, session, Response, jsonify
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

_MARKET_REGIONS = {
    'North America': {'US', 'CA', 'MX'},
    'Latin America': {
        'BR', 'AR', 'CL', 'CO', 'PE', 'VE', 'EC', 'BO', 'PY', 'UY',
        'CR', 'GT', 'HN', 'NI', 'PA', 'SV', 'DO', 'JM', 'TT', 'CU', 'BB',
    },
    'Europe': {
        'GB', 'DE', 'FR', 'ES', 'IT', 'NL', 'BE', 'PT', 'SE', 'NO', 'DK',
        'FI', 'PL', 'AT', 'CH', 'CZ', 'HU', 'RO', 'GR', 'HR', 'BG', 'SK',
        'SI', 'EE', 'LV', 'LT', 'IS', 'IE', 'LU', 'MT', 'CY', 'RS', 'UA',
    },
    'Asia Pacific': {
        'JP', 'AU', 'NZ', 'SG', 'MY', 'TH', 'ID', 'PH', 'VN', 'KR',
        'TW', 'HK', 'IN', 'PK', 'BD', 'LK', 'NP',
    },
    'Middle East & Africa': {
        'ZA', 'NG', 'GH', 'KE', 'EG', 'MA', 'TN', 'AE', 'SA', 'QA',
        'KW', 'BH', 'IL', 'TR', 'JO', 'LB', 'OM',
    },
}


def _compute_market_breakdown(markets):
    market_set = set(markets)
    breakdown = {}
    uncategorized = set(market_set)
    for region, codes in _MARKET_REGIONS.items():
        matched = market_set & codes
        if matched:
            breakdown[region] = len(matched)
        uncategorized -= codes
    if uncategorized:
        breakdown['Other'] = len(uncategorized)
    return breakdown


@main_bp.route('/')
def index():
    return redirect(url_for('main.search_artist'))


@main_bp.route('/artist/<artist_id>')
def artist_intel(artist_id):
    """Comprehensive artist intelligence dashboard."""
    sp = get_spotify_client_credentials_client()
    if not sp:
        flash('Spotify API client could not be initialized.', 'error')
        return redirect(url_for('main.search_artist'))

    try:
        artist = sp.artist(artist_id)
        if not artist:
            flash('Artist not found.', 'error')
            return redirect(url_for('main.search_artist'))
    except Exception as e:
        flash(f'Error fetching artist: {e}', 'error')
        return redirect(url_for('main.search_artist'))

    top_tracks = []
    try:
        top_tracks = sp.artist_top_tracks(artist_id, country='US').get('tracks', [])
    except Exception as e:
        print(f"[ArtistIntel] Error fetching top tracks: {e}")

    releases = []
    release_stats = {}
    available_markets = []
    try:
        simplified = sp.artist_albums(
            artist_id, album_type='album,single', limit=20
        ).get('items', [])
        if simplified:
            releases = fetch_release_details(sp, simplified)
            release_stats = calculate_release_stats(releases)
            if releases:
                available_markets = releases[0].get('available_markets', [])
    except Exception as e:
        print(f"[ArtistIntel] Error fetching releases: {e}")

    market_breakdown = _compute_market_breakdown(available_markets)
    total_markets = len(available_markets)

    labels_seen = set()
    artist_labels = []
    for release in releases:
        label_name = release.get('label')
        if label_name and label_name not in labels_seen:
            labels_seen.add(label_name)
            artist_labels.append(label_name)

    return render_template(
        'artist_intel.html',
        artist=artist,
        top_tracks=top_tracks,
        releases=releases,
        release_stats=release_stats,
        available_markets=available_markets,
        total_markets=total_markets,
        market_breakdown=market_breakdown,
        artist_labels=artist_labels,
    )


@main_bp.route('/api/artist/<artist_id>/intel')
def artist_intel_api(artist_id):
    """
    Async endpoint returning supplemental data: Last.fm tags/events,
    MusicBrainz contacts, Wikipedia bio. Called by the frontend after render.
    """
    sp = get_spotify_client_credentials_client()
    if not sp:
        return jsonify({'error': 'Spotify unavailable'}), 503

    try:
        artist = sp.artist(artist_id)
        artist_name = artist.get('name', '') if artist else ''
    except Exception:
        return jsonify({'error': 'Artist not found'}), 404

    result = {
        'lastfm_tags': [],
        'lastfm_events': [],
        'musicbrainz': None,
        'wikipedia': None,
    }

    try:
        tags = scrape_lastfm_tags(artist_name)
        result['lastfm_tags'] = tags if tags else []
    except Exception as e:
        print(f"[IntelAPI] Last.fm tags error: {e}")

    try:
        events = scrape_lastfm_upcoming_events(artist_name)
        result['lastfm_events'] = events if events else []
    except Exception as e:
        print(f"[IntelAPI] Last.fm events error: {e}")

    try:
        from ..musicbrainz.api import find_artist_mbid, get_artist_intel
        mbid = find_artist_mbid(artist_name)
        if mbid:
            result['musicbrainz'] = get_artist_intel(mbid)
    except Exception as e:
        print(f"[IntelAPI] MusicBrainz error: {e}")
        traceback.print_exc()

    try:
        from ..wikipedia.api import get_artist_summary
        wiki_url = None
        if result['musicbrainz']:
            wiki_url = result['musicbrainz'].get('wikipedia_url')
        result['wikipedia'] = get_artist_summary(artist_name, wiki_url)
    except Exception as e:
        print(f"[IntelAPI] Wikipedia error: {e}")

    return jsonify(result)


@main_bp.route('/search', methods=['GET'])
def search_artist():
    sp = get_spotify_client_credentials_client()
    query = request.args.get('query', '').strip()
    error = None

    if query:
        if not sp:
            error = 'Spotify API client could not be initialized.'
        else:
            try:
                results = sp.search(q=query, type='artist', limit=1)
                if results and results['artists']['items']:
                    artist_id = results['artists']['items'][0]['id']
                    return redirect(url_for('main.artist_intel', artist_id=artist_id))
                else:
                    error = f'No artist found matching "{query}".'
            except Exception as e:
                error = f'Search error: {e}'
                traceback.print_exc()

    return render_template('search.html', query=query, error=error)


@main_bp.route('/similar-artists/<artist_id>', methods=['GET'])
def similar_artists(artist_id):
    sp = get_spotify_client_credentials_client()
    if not sp:
        flash('Spotify API client could not be initialized.', 'error')
        return redirect(url_for('main.search_artist'))

    try:
        min_followers = request.args.get('min_followers', default=None, type=int)
        max_followers = request.args.get('max_followers', default=None, type=int)
        min_popularity = request.args.get('min_popularity', default=None, type=int)
        max_popularity = request.args.get('max_popularity', default=None, type=int)
        page = request.args.get('page', default=1, type=int)
        if page < 1:
            page = 1
    except ValueError:
        flash("Invalid filter or page value.", "warning")
        min_followers = max_followers = min_popularity = max_popularity = None
        page = 1

    per_page = 24
    source_artist = None
    pool_key = f"similar_artists_pool_{artist_id}"

    try:
        source_artist = sp.artist(artist_id)
        source_artist_name = source_artist.get('name', 'Selected Artist')
        source_artist_genres = source_artist.get('genres', [])

        spotify_genre_artists = fetch_similar_artists_by_genre(sp, artist_id, source_artist_name, source_artist_genres)
        lastfm_names = scrape_all_lastfm_similar_artists_names(source_artist_name, max_pages=5)
        lastfm_spotify_artists = fetch_spotify_details_for_names(sp, lastfm_names or [])

        combined_artists_map = {a['id']: a for a in spotify_genre_artists if a and a.get('id') != artist_id}
        for a in lastfm_spotify_artists:
            if a and a.get('id') != artist_id:
                combined_artists_map[a['id']] = a
        combined_pool = list(combined_artists_map.values())

        session[pool_key] = combined_pool

        total_pool_size = len(combined_pool)
        aggregated_genres = {}
        for a in combined_pool:
            for genre in a.get('genres', []):
                gk = genre.lower()
                aggregated_genres[gk] = aggregated_genres.get(gk, {'display_name': genre, 'count': 0})
                aggregated_genres[gk]['count'] += 1
        sorted_genres = sorted(aggregated_genres.values(), key=lambda x: x['count'], reverse=True)

        filtered_artists = []
        for a in combined_pool:
            followers = a.get('followers', {}).get('total')
            popularity = a.get('popularity')
            if min_followers is not None and (followers is None or followers < min_followers): continue
            if max_followers is not None and (followers is None or followers > max_followers): continue
            if min_popularity is not None and (popularity is None or popularity < min_popularity): continue
            if max_popularity is not None and (popularity is None or popularity > max_popularity): continue
            filtered_artists.append(a)

        filtered_artists.sort(key=lambda a: a.get('popularity', 0), reverse=True)
        total_artists = len(filtered_artists)
        total_pages = math.ceil(total_artists / per_page)
        offset = (page - 1) * per_page
        artists_on_page = filtered_artists[offset: offset + per_page]

        return render_template(
            'similar_artists.html',
            artists=artists_on_page,
            source_artist_name=source_artist_name,
            source_artist_id=artist_id,
            search_genres=source_artist_genres[:3],
            min_followers=min_followers, max_followers=max_followers,
            min_popularity=min_popularity, max_popularity=max_popularity,
            current_page=page, total_pages=total_pages,
            total_artists=total_artists, per_page=per_page,
            aggregated_genres=sorted_genres, total_pool_size=total_pool_size,
        )

    except Exception as e:
        print(f"Unexpected error in similar_artists route: {e}")
        traceback.print_exc()
        flash('An unexpected error occurred while finding similar artists.', 'error')
        source_name = source_artist.get('name', 'the artist') if source_artist else 'the artist'
        return render_template(
            'similar_artists.html', artists=[], source_artist_name=source_name,
            source_artist_id=artist_id, aggregated_genres=[], total_pool_size=0,
        )


@main_bp.route('/download-similar/<artist_id>')
def download_similar_artists(artist_id):
    sp = get_spotify_client_credentials_client()
    if not sp:
        return "Spotify API client could not be initialized.", 500

    try:
        pool_key = f"similar_artists_pool_{artist_id}"
        combined_pool = session.get(pool_key)

        source_artist = sp.artist(artist_id)
        source_artist_name = source_artist.get('name', 'artist').replace(" ", "_")

        if combined_pool is None:
            source_artist_genres = source_artist.get('genres', [])
            real_source_name = source_artist.get('name', 'artist')
            spotify_genre_artists = fetch_similar_artists_by_genre(sp, artist_id, real_source_name, source_artist_genres)
            lastfm_names = scrape_all_lastfm_similar_artists_names(real_source_name, max_pages=5)
            lastfm_spotify_artists = fetch_spotify_details_for_names(sp, lastfm_names or [])
            combined_artists_map = {a['id']: a for a in spotify_genre_artists if a and a.get('id') != artist_id}
            for a in lastfm_spotify_artists:
                if a and a.get('id') != artist_id:
                    combined_artists_map[a['id']] = a
            combined_pool = list(combined_artists_map.values())

        min_followers = request.args.get('min_followers', default=None, type=int)
        max_followers = request.args.get('max_followers', default=None, type=int)
        min_popularity = request.args.get('min_popularity', default=None, type=int)
        max_popularity = request.args.get('max_popularity', default=None, type=int)
        columns = request.args.get('columns', 'name,followers,popularity').split(',')
        file_format = request.args.get('format', 'csv')

        filtered_artists = []
        for a in combined_pool:
            followers = a.get('followers', {}).get('total')
            popularity = a.get('popularity')
            if min_followers is not None and (followers is None or followers < min_followers): continue
            if max_followers is not None and (followers is None or followers > max_followers): continue
            if min_popularity is not None and (popularity is None or popularity < min_popularity): continue
            if max_popularity is not None and (popularity is None or popularity > max_popularity): continue
            filtered_artists.append(a)

        if not filtered_artists:
            return "No artists match the specified filters.", 404

        df = pd.DataFrame(filtered_artists)
        column_mappers = {
            'name': lambda r: r.get('name', 'N/A'),
            'followers': lambda r: r.get('followers', {}).get('total'),
            'popularity': lambda r: r.get('popularity'),
            'genres': lambda r: ', '.join(r.get('genres', [])),
            'url': lambda r: r.get('external_urls', {}).get('spotify'),
            'image_url': lambda r: r['images'][0]['url'] if r.get('images') else None,
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
            headers={"Content-Disposition": f"attachment;filename={filename}"},
        )

    except Exception as e:
        print(f"Error during file download generation: {e}")
        traceback.print_exc()
        return "An unexpected error occurred while generating the file.", 500
