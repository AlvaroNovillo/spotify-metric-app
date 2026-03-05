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
    scrape_lastfm_tags,
    scrape_lastfm_artist_stats,
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


def _generate_ai_bio(artist, lastfm_tags, lastfm_stats, mb_data, wiki_extract, artist_labels, release_stats):
    """Use Gemini to synthesize all available data into a professional artist profile."""
    try:
        import google.generativeai as genai
        model_name = current_app.config.get('GEMINI_MODEL_NAME', 'gemini-3-flash-preview')
        model = genai.GenerativeModel(model_name)

        followers = artist.get('followers', {}).get('total', 0) or 0
        genres = artist.get('genres', [])
        popularity = artist.get('popularity', 0) or 0
        name = artist.get('name', '')

        mb_area = mb_data.get('area') or mb_data.get('begin_area') if mb_data else None
        mb_type = mb_data.get('type') if mb_data else None

        rstats = release_stats or {}
        listeners_fmt = f"{lastfm_stats.get('listeners'):,}" if lastfm_stats.get('listeners') else 'N/A'
        scrobbles_fmt = f"{lastfm_stats.get('scrobbles'):,}" if lastfm_stats.get('scrobbles') else 'N/A'
        prompt = f"""You are a senior music industry analyst. Based on the following data, write a concise 3-paragraph artist intelligence profile. Use specific data points, professional tone, and NO generic filler phrases like "unique blend" or "sonic landscape".

ARTIST DATA:
Name: {name}
Type: {mb_type or 'Artist'}
Origin: {mb_area or 'Unknown'}
Spotify Genres: {', '.join(genres) or 'Not classified'}
Last.fm Tags: {', '.join(lastfm_tags[:12]) if lastfm_tags else 'N/A'}
Spotify Followers: {followers:,}
Popularity Score: {popularity}/100
Last.fm Weekly Listeners: {listeners_fmt}
Last.fm Total Scrobbles: {scrobbles_fmt}
Total Releases: {rstats.get('total_releases', 'N/A')}
Active Since: {rstats.get('first_release_year', 'Unknown')}
Record Labels: {', '.join(artist_labels) if artist_labels else 'Independent'}
Wikipedia Summary: {wiki_extract[:400] if wiki_extract else 'Not available'}

WRITE:
Paragraph 1 — Musical Identity: Genre positioning, artistic style, sound characteristics, key influences visible in the data.
Paragraph 2 — Career Metrics: What the numbers tell us — follower count relative to popularity, release cadence, career stage.
Paragraph 3 — Market Position & Opportunity: Target audience profile, strongest geographic markets, commercial potential.

Keep total under 280 words. Return plain text only, no headers, no bullet points."""

        response = model.generate_content(prompt)
        return response.text.strip() if response.text else None
    except Exception as e:
        print(f"[AI Bio] Gemini error: {e}")
        return None


@main_bp.route('/api/artist/<artist_id>/intel')
def artist_intel_api(artist_id):
    """
    Async endpoint: Last.fm tags/stats/events, MusicBrainz contacts,
    Wikipedia data, and Gemini AI bio. Called by the frontend after render.
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
        'lastfm_stats': {},
        'musicbrainz': None,
        'wikipedia': None,
        'ai_bio': None,
    }

    try:
        result['lastfm_tags'] = scrape_lastfm_tags(artist_name) or []
    except Exception as e:
        print(f"[IntelAPI] Last.fm tags error: {e}")

    try:
        result['lastfm_events'] = scrape_lastfm_upcoming_events(artist_name) or []
    except Exception as e:
        print(f"[IntelAPI] Last.fm events error: {e}")

    try:
        result['lastfm_stats'] = scrape_lastfm_artist_stats(artist_name) or {}
    except Exception as e:
        print(f"[IntelAPI] Last.fm stats error: {e}")

    try:
        from ..musicbrainz.api import find_artist_mbid, get_artist_intel
        mbid = find_artist_mbid(artist_name)
        if mbid:
            result['musicbrainz'] = get_artist_intel(mbid)
    except Exception as e:
        print(f"[IntelAPI] MusicBrainz error: {e}")
        traceback.print_exc()

    wiki_extract = None
    try:
        from ..wikipedia.api import get_artist_summary
        wiki_url = result['musicbrainz'].get('wikipedia_url') if result['musicbrainz'] else None
        wiki_data = get_artist_summary(artist_name, wiki_url)
        result['wikipedia'] = wiki_data
        if wiki_data:
            wiki_extract = wiki_data.get('extract')
    except Exception as e:
        print(f"[IntelAPI] Wikipedia error: {e}")

    # Gather labels from artist's recent releases for AI bio context
    artist_labels = []
    try:
        simplified = sp.artist_albums(artist_id, album_type='album,single', limit=5).get('items', [])
        seen = set()
        for rel in simplified:
            full = sp.album(rel['id'])
            lbl = full.get('label')
            if lbl and lbl not in seen:
                seen.add(lbl)
                artist_labels.append(lbl)
    except Exception:
        pass

    release_stats = {}
    try:
        from ..spotify.utils import calculate_release_stats
        simplified_all = sp.artist_albums(artist_id, album_type='album,single', limit=20).get('items', [])
        release_stats = calculate_release_stats(simplified_all) if simplified_all else {}
    except Exception:
        pass

    try:
        result['ai_bio'] = _generate_ai_bio(
            artist, result['lastfm_tags'], result['lastfm_stats'],
            result['musicbrainz'], wiki_extract, artist_labels, release_stats
        )
    except Exception as e:
        print(f"[IntelAPI] AI bio error: {e}")

    return jsonify(result)


@main_bp.route('/artist/<artist_id>/pitch')
def artist_pitch(artist_id):
    """Pitching hub: Spotify playlist discovery + label outreach + press targets."""
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
        flash(f'Error: {e}', 'error')
        return redirect(url_for('main.search_artist'))

    top_tracks = []
    try:
        top_tracks = sp.artist_top_tracks(artist_id, country='US').get('tracks', [])[:5]
    except Exception:
        pass

    return render_template('pitch.html', artist=artist, top_tracks=top_tracks)


@main_bp.route('/api/artist/<artist_id>/playlists')
def artist_playlists_api(artist_id):
    """
    Search Spotify for playlists matching genre/artist keywords.
    Returns user-curated playlists sorted by track count (no PlaylistSupply needed).
    """
    sp = get_spotify_client_credentials_client()
    if not sp:
        return jsonify({'error': 'Spotify unavailable'}), 503

    try:
        artist = sp.artist(artist_id)
        artist_name = artist.get('name', '') if artist else ''
        genres = artist.get('genres', []) if artist else []
    except Exception:
        return jsonify({'error': 'Artist not found'}), 404

    # Build keyword list from genres + artist name
    keywords = list({artist_name} | {g for g in genres[:6]})

    found = {}
    for kw in keywords[:15]:
        try:
            results = sp.search(q=kw, type='playlist', limit=20)
            items = results.get('playlists', {}).get('items', []) or []
            for pl in items:
                if not pl or not pl.get('id'):
                    continue
                owner = pl.get('owner', {}) or {}
                owner_id = owner.get('id', '')
                # Skip official Spotify editorial playlists
                if owner_id.lower() in ('spotify', 'spotifycharts', 'spotifypodcasts',
                                        'spotify_germany', 'spotify_france'):
                    continue
                pl_id = pl['id']
                tracks_total = pl.get('tracks', {}).get('total', 0) or 0
                if pl_id not in found:
                    found[pl_id] = {
                        'id': pl_id,
                        'name': pl.get('name', ''),
                        'description': (pl.get('description') or '')[:200],
                        'tracks_total': tracks_total,
                        'owner_name': owner.get('display_name') or owner_id or 'Unknown',
                        'owner_id': owner_id,
                        'url': (pl.get('external_urls') or {}).get('spotify', ''),
                        'image': pl['images'][0]['url'] if pl.get('images') else None,
                        'found_by': [kw],
                    }
                else:
                    if kw not in found[pl_id]['found_by']:
                        found[pl_id]['found_by'].append(kw)
            time.sleep(0.12)
        except Exception as e:
            print(f"[PlaylistAPI] Error for keyword '{kw}': {e}")

    # Sort: most keyword matches first, then by track count
    sorted_pls = sorted(
        found.values(),
        key=lambda p: (len(p['found_by']), p.get('tracks_total', 0)),
        reverse=True
    )

    # Enrich top 40 with actual follower counts
    enriched = []
    for pl in sorted_pls[:40]:
        try:
            full = sp.playlist(pl['id'], fields='followers,id')
            pl['followers'] = (full.get('followers') or {}).get('total', 0)
        except Exception:
            pl['followers'] = 0
        enriched.append(pl)
        time.sleep(0.05)

    # Re-sort by followers for top 40, keep rest at end
    enriched.sort(key=lambda p: p.get('followers', 0), reverse=True)
    final = enriched + sorted_pls[40:]

    return jsonify({'playlists': final[:200], 'keywords_used': keywords})


@main_bp.route('/api/artist/<artist_id>/label-contacts')
def label_contacts_api(artist_id):
    """
    Aggregate labels from the artist's own releases + top similar artists,
    then enrich each with MusicBrainz contact data (website, social links).
    """
    sp = get_spotify_client_credentials_client()
    if not sp:
        return jsonify({'error': 'Spotify unavailable'}), 503

    try:
        artist = sp.artist(artist_id)
        artist_name = artist.get('name', '') if artist else ''
        artist_genres = artist.get('genres', []) if artist else []
    except Exception:
        return jsonify({'error': 'Artist not found'}), 404

    labels_map = {}  # label_name -> {name, artists, website, social_links, country, type}

    def _add_label(label_name, credited_artist_name):
        if not label_name or len(label_name) < 2:
            return
        # Skip distributor-only services
        skip_keywords = ['distrokid', 'tunecore', 'cdbaby', 'amuse', 'routenote',
                         'soundrop', 'emubands', 'ditto', 'record union']
        if any(kw in label_name.lower() for kw in skip_keywords):
            return
        if label_name not in labels_map:
            labels_map[label_name] = {
                'name': label_name,
                'artists': [],
                'website': None,
                'social_links': [],
                'country': None,
                'type': None,
                'mb_enriched': False,
            }
        if credited_artist_name and credited_artist_name not in labels_map[label_name]['artists']:
            labels_map[label_name]['artists'].append(credited_artist_name)

    # Artist's own releases
    try:
        own_releases = sp.artist_albums(artist_id, album_type='album,single', limit=10).get('items', [])
        for rel in own_releases[:6]:
            try:
                full = sp.album(rel['id'])
                _add_label(full.get('label'), artist_name)
            except Exception:
                pass
            time.sleep(0.05)
    except Exception as e:
        print(f"[LabelContacts] Error fetching own releases: {e}")

    # Top similar artists' labels (genre-based Spotify search)
    try:
        similar = fetch_similar_artists_by_genre(sp, artist_id, artist_name, artist_genres,
                                                 candidates_per_genre=15)
        # Sort by popularity, take top 12
        similar.sort(key=lambda a: a.get('popularity', 0), reverse=True)
        for sim in similar[:12]:
            try:
                sim_releases = sp.artist_albums(sim['id'], album_type='album,single', limit=3)
                for rel in (sim_releases.get('items') or [])[:2]:
                    try:
                        full = sp.album(rel['id'])
                        _add_label(full.get('label'), sim.get('name', ''))
                    except Exception:
                        pass
                time.sleep(0.05)
            except Exception:
                pass
    except Exception as e:
        print(f"[LabelContacts] Error fetching similar artists: {e}")

    print(f"[LabelContacts] Found {len(labels_map)} unique labels before MB enrichment")

    # Enrich with MusicBrainz (rate-limited: 1 req/sec, cap at 15 labels)
    try:
        from ..musicbrainz.api import get_label_contacts
        enrich_count = 0
        for label_name, label_data in labels_map.items():
            if enrich_count >= 15:
                break
            try:
                mb = get_label_contacts(label_name)
                if mb:
                    label_data['website'] = mb.get('website')
                    label_data['social_links'] = mb.get('social_links', [])
                    label_data['country'] = mb.get('country')
                    label_data['type'] = mb.get('type')
                    label_data['mb_enriched'] = True
                enrich_count += 1
            except Exception as e:
                print(f"[LabelContacts] MB error for '{label_name}': {e}")
    except Exception as e:
        print(f"[LabelContacts] MusicBrainz module error: {e}")
        traceback.print_exc()

    # Sort: labels with websites first, then by number of signed artists
    sorted_labels = sorted(
        labels_map.values(),
        key=lambda l: (bool(l.get('website')), len(l.get('artists', []))),
        reverse=True
    )

    return jsonify({'labels': sorted_labels})


@main_bp.route('/api/artist/<artist_id>/label-pitch', methods=['POST'])
def label_pitch_api(artist_id):
    """
    Generate a personalised pitch email for a specific label using Gemini.
    POST body: { label_name, label_type, label_country, label_artists, label_website }
    """
    if not current_app.config.get('GEMINI_API_KEY'):
        return jsonify({'error': 'AI not configured'}), 500

    sp = get_spotify_client_credentials_client()
    if not sp:
        return jsonify({'error': 'Spotify unavailable'}), 503

    data = request.get_json(silent=True) or {}
    label_name = data.get('label_name', '')
    label_type = data.get('label_type', '')
    label_country = data.get('label_country', '')
    label_artists = data.get('label_artists', [])
    label_website = data.get('label_website', '')

    if not label_name:
        return jsonify({'error': 'label_name required'}), 400

    try:
        artist = sp.artist(artist_id)
        if not artist:
            return jsonify({'error': 'Artist not found'}), 404
    except Exception:
        return jsonify({'error': 'Artist not found'}), 404

    artist_name = artist.get('name', '')
    genres = artist.get('genres', [])
    followers = (artist.get('followers') or {}).get('total', 0)
    popularity = artist.get('popularity', 0)

    lastfm_tags = []
    try:
        lastfm_tags = scrape_lastfm_tags(artist_name) or []
    except Exception:
        pass

    release_stats_obj = {}
    try:
        simplified = sp.artist_albums(artist_id, album_type='album,single', limit=5).get('items', [])
        from ..spotify.utils import calculate_release_stats
        release_stats_obj = calculate_release_stats(simplified) or {}
    except Exception:
        pass

    try:
        import google.generativeai as genai
        model_name = current_app.config.get('GEMINI_MODEL_NAME', 'gemini-3-flash-preview')
        model = genai.GenerativeModel(model_name)

        roster_str = ', '.join(label_artists[:8]) if label_artists else 'unknown roster'
        prompt = f"""You are a professional music industry consultant writing a concise, compelling cold-pitch email from an artist to a record label.

ARTIST PROFILE:
- Name: {artist_name}
- Genres: {', '.join(genres) or 'Unknown'}
- Last.fm Tags: {', '.join(lastfm_tags[:8]) or 'N/A'}
- Spotify Followers: {followers:,}
- Popularity Score: {popularity}/100
- Active Since: {release_stats_obj.get('first_release_year', 'Unknown')}
- Total Releases: {release_stats_obj.get('total_releases', 'N/A')}

TARGET LABEL:
- Name: {label_name}
- Type: {label_type or 'Independent'}
- Country: {label_country or 'Unknown'}
- Known Roster: {roster_str}
- Website: {label_website or 'Unknown'}

Write a professional cold pitch email. Rules:
1. Subject line first, then blank line, then email body
2. Address the label by name, not generically
3. Reference 1-2 specific artists from their roster to show you know the label
4. Highlight the 2-3 most compelling data points from the artist profile
5. Keep it under 200 words total
6. End with a clear call to action
7. Use [ARTIST NAME] as the signature placeholder
8. Professional but not stiff — music industry tone

Return ONLY the email (subject + body). No explanations."""

        response = model.generate_content(prompt)
        pitch_text = response.text.strip() if response.text else ''
        return jsonify({'pitch': pitch_text})
    except Exception as e:
        print(f"[LabelPitch] Gemini error: {e}")
        return jsonify({'error': f'Generation failed: {e}'}), 500


@main_bp.route('/artist/<artist_id>/marketing')
def artist_marketing(artist_id):
    """AI-generated comprehensive marketing strategy page."""
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
        flash(f'Error: {e}', 'error')
        return redirect(url_for('main.search_artist'))

    return render_template('marketing.html', artist=artist)


@main_bp.route('/api/artist/<artist_id>/marketing-strategy')
def marketing_strategy_api(artist_id):
    """Generate a full AI marketing strategy using Gemini."""
    sp = get_spotify_client_credentials_client()
    if not sp:
        return jsonify({'error': 'Spotify unavailable'}), 503

    if not current_app.config.get('GEMINI_API_KEY'):
        return jsonify({'error': 'AI not configured'}), 500

    try:
        artist = sp.artist(artist_id)
        if not artist:
            return jsonify({'error': 'Artist not found'}), 404
    except Exception:
        return jsonify({'error': 'Artist not found'}), 404

    artist_name = artist.get('name', '')
    genres = artist.get('genres', [])
    followers = (artist.get('followers') or {}).get('total', 0)
    popularity = artist.get('popularity', 0)

    # Gather extra data
    lastfm_tags, lastfm_stats, artist_labels, release_stats_obj = [], {}, [], {}
    available_markets = []
    try:
        lastfm_tags = scrape_lastfm_tags(artist_name) or []
    except Exception:
        pass
    try:
        lastfm_stats = scrape_lastfm_artist_stats(artist_name) or {}
    except Exception:
        pass
    try:
        simplified = sp.artist_albums(artist_id, album_type='album,single', limit=10).get('items', [])
        seen_labels = set()
        for rel in simplified[:5]:
            full = sp.album(rel['id'])
            lbl = full.get('label')
            if lbl and lbl not in seen_labels:
                seen_labels.add(lbl)
                artist_labels.append(lbl)
        from ..spotify.utils import calculate_release_stats
        release_stats_obj = calculate_release_stats(simplified) or {}
        if simplified:
            first_full = sp.album(simplified[0]['id'])
            available_markets = first_full.get('available_markets', [])
    except Exception:
        pass

    mb_area = None
    try:
        from ..musicbrainz.api import find_artist_mbid, get_artist_intel
        mbid = find_artist_mbid(artist_name)
        if mbid:
            mb = get_artist_intel(mbid)
            if mb:
                mb_area = mb.get('area') or mb.get('begin_area')
    except Exception:
        pass

    market_breakdown = _compute_market_breakdown(available_markets)

    import google.generativeai as genai
    model_name = current_app.config.get('GEMINI_MODEL_NAME', 'gemini-3-flash-preview')
    model = genai.GenerativeModel(model_name)

    prompt = f"""You are a senior music industry strategist with 20 years of experience in artist development, A&R, and marketing. Generate a detailed, actionable marketing strategy.

ARTIST INTELLIGENCE DATA:
- Name: {artist_name}
- Genres: {', '.join(genres) or 'Unknown'}
- Last.fm Tags: {', '.join(lastfm_tags[:15]) or 'N/A'}
- Spotify Followers: {followers:,}
- Spotify Popularity: {popularity}/100
- Last.fm Weekly Listeners: {lastfm_stats.get('listeners', 'N/A')}
- Last.fm Total Scrobbles: {lastfm_stats.get('scrobbles', 'N/A')}
- Total Releases: {release_stats_obj.get('total_releases', 'N/A')}
- Active Since: {release_stats_obj.get('first_release_year', 'Unknown')}
- Record Labels: {', '.join(artist_labels) or 'Independent'}
- Origin / Area: {mb_area or 'Unknown'}
- Market Presence: {', '.join(f'{k} ({v} countries)' for k, v in market_breakdown.items()) or 'Unknown'}

Return ONLY a valid JSON object. No markdown. No explanation. Exactly this schema:
{{
  "positioning": "2-sentence market positioning statement",
  "career_stage": "Underground | Emerging | Rising | Established | Mainstream",
  "target_audience": {{
    "primary": "Primary audience description",
    "secondary": "Secondary audience",
    "psychographic": "Values, lifestyle, listening habits",
    "top_platforms": ["Platform1", "Platform2", "Platform3"]
  }},
  "geographic_strategy": {{
    "priority_markets": ["Country1", "Country2", "Country3"],
    "reasoning": "Why these markets based on the data",
    "expansion_markets": ["Country1", "Country2"],
    "approach": "Specific geographic expansion approach"
  }},
  "playlist_strategy": {{
    "target_tier": "micro (< 5K followers) | mid (5K-50K) | major (50K+) | all",
    "genres_to_pitch": ["genre1", "genre2", "genre3"],
    "mood_keywords": ["mood1", "mood2", "mood3"],
    "curator_profile": "Description of ideal playlist curator to target",
    "pitch_tip": "Specific actionable pitch advice"
  }},
  "press_targets": [
    {{"outlet": "Publication name", "why": "Why this publication fits"}},
    {{"outlet": "Publication name", "why": "Why this publication fits"}},
    {{"outlet": "Publication name", "why": "Why this publication fits"}},
    {{"outlet": "Publication name", "why": "Why this publication fits"}}
  ],
  "social_strategy": {{
    "primary_platform": "Most important platform",
    "why": "Why this platform fits this artist",
    "content_pillars": ["Pillar1", "Pillar2", "Pillar3"],
    "posting_cadence": "Recommended frequency and format",
    "growth_tactic": "Specific growth tactic for this genre"
  }},
  "label_strategy": {{
    "recommendation": "Stay indie | Approach indie labels | Approach major labels",
    "reasoning": "Why, based on current metrics",
    "target_label_types": ["Type of label 1", "Type of label 2"],
    "approach": "How to make the pitch"
  }},
  "collaboration_strategy": {{
    "collab_type": "Feature | Joint EP | Tour | Co-write | Remix",
    "target_career_stage": "What level of artist to approach",
    "genre_adjacency": "Which adjacent genres to target for cross-promotion",
    "approach": "How to initiate"
  }},
  "ninety_day_plan": [
    {{"week": "1–2", "focus": "Focus area", "actions": ["Action 1", "Action 2", "Action 3"]}},
    {{"week": "3–4", "focus": "Focus area", "actions": ["Action 1", "Action 2", "Action 3"]}},
    {{"week": "5–8", "focus": "Focus area", "actions": ["Action 1", "Action 2"]}},
    {{"week": "9–12", "focus": "Focus area", "actions": ["Action 1", "Action 2"]}}
  ],
  "quick_wins": ["Specific quick win 1", "Specific quick win 2", "Specific quick win 3"]
}}"""

    try:
        import re as re_module
        response = model.generate_content(prompt)
        text = response.text.strip()
        # Strip any markdown code fences
        text = re_module.sub(r'^```(?:json)?\s*', '', text)
        text = re_module.sub(r'\s*```$', '', text)
        import json as json_module
        strategy = json_module.loads(text)
        return jsonify({'strategy': strategy})
    except Exception as e:
        print(f"[MarketingAPI] Gemini/parse error: {e}")
        traceback.print_exc()
        return jsonify({'error': f'Strategy generation failed: {e}'}), 500


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
