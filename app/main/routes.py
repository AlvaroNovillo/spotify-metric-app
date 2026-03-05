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

def _ai_call(prompt: str) -> str:
    """Call Gemini and return the text response."""
    import google.generativeai as genai
    api_key = current_app.config.get('GEMINI_API_KEY')
    if not api_key:
        raise RuntimeError('GEMINI_API_KEY not configured')
    model_name = current_app.config.get('GEMINI_MODEL_NAME')
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name)
    response = model.generate_content(prompt)
    return response.text.strip() if response.text else ''


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
    """Use Claude to synthesize all available data into a professional artist profile."""
    try:

        followers = artist.get('followers', {}).get('total', 0) or 0
        genres = artist.get('genres', [])
        popularity = artist.get('popularity', 0) or 0
        name = artist.get('name', '')

        mb_area = mb_data.get('area') or mb_data.get('begin_area') if mb_data else None
        mb_type = mb_data.get('type') if mb_data else None

        rstats = release_stats or {}
        listeners_fmt = f"{lastfm_stats.get('listeners'):,}" if lastfm_stats.get('listeners') else 'N/A'
        scrobbles_fmt = f"{lastfm_stats.get('scrobbles'):,}" if lastfm_stats.get('scrobbles') else 'N/A'
        prompt = f"""You are a senior music industry analyst. Analyze the data below and return a structured JSON object — nothing else, no markdown, no code fences, just the raw JSON.

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

Return exactly this JSON structure (all strings, use real data — NO filler phrases like "unique blend"):
{{
  "genre_profile": "2–3 sentence description of their exact genre positioning, sub-genres, sonic characteristics and key influences backed by the data.",
  "target_audience": "2–3 sentences describing the likely fan demographics, listener behavior (e.g. streaming depth vs casual), geographic concentration based on Last.fm and Spotify data.",
  "market_snapshot": "2–3 sentences on where they stand commercially: follower count vs popularity score analysis, release cadence verdict, career stage (emerging / mid-tier / established).",
  "label_career": "1–2 sentences on their label history and what it signals about their deal structure or independence."
}}"""

        raw = _ai_call(prompt)
        if not raw:
            return None
        # Strip accidental markdown fences
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[-1]
            raw = raw.rsplit('```', 1)[0].strip()
        import json as _json
        try:
            return _json.loads(raw)
        except Exception:
            return raw
    except Exception as e:
        print(f"[AI Bio] Claude error: {e}")
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

        pitch_text = _ai_call(prompt)
        return jsonify({'pitch': pitch_text})
    except Exception as e:
        print(f"[LabelPitch] Claude error: {e}")
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

    # ── Benchmark peers: similar artists with stronger metrics ──────────
    benchmark_peers = []
    try:
        related = sp.artist_related_artists(artist_id).get('artists', [])
        # Keep only artists meaningfully ahead of the target
        candidates = [
            a for a in related
            if (a.get('popularity', 0) >= popularity + 8
                or (a.get('followers', {}).get('total', 0) or 0) >= max(followers, 1) * 2)
            and a.get('popularity', 0) > 0
        ]
        candidates.sort(
            key=lambda x: (x.get('followers', {}).get('total', 0) or 0),
            reverse=True
        )
        from ..spotify.utils import calculate_release_stats as _crs
        for peer in candidates[:5]:
            peer_id = peer['id']
            peer_followers = (peer.get('followers', {}).get('total', 0) or 0)
            peer_name = peer.get('name', '')
            peer_genres = peer.get('genres', [])
            peer_popularity = peer.get('popularity', 0)
            peer_stats = {}
            try:
                peer_releases = sp.artist_albums(peer_id, album_type='album,single', limit=10).get('items', [])
                peer_stats = _crs(peer_releases) or {}
            except Exception:
                pass
            benchmark_peers.append({
                'name': peer_name,
                'followers': peer_followers,
                'popularity': peer_popularity,
                'genres': peer_genres[:3],
                'total_releases': peer_stats.get('total_releases', 'N/A'),
                'active_since': peer_stats.get('first_release_year', 'Unknown'),
                'followers_multiplier': round(peer_followers / max(followers, 1), 1),
            })
    except Exception as e:
        print(f"[MarketingAPI] Benchmark peers error: {e}")

    def _fmt_peer(p):
        genres_str = ', '.join(p['genres']) if p['genres'] else 'similar genre'
        f = p['followers']
        f_str = f"{f/1_000_000:.1f}M" if f >= 1_000_000 else f"{f/1_000:.0f}K" if f >= 1_000 else str(f)
        return (
            f"  - {p['name']}: {f_str} followers ({p['followers_multiplier']}× target), "
            f"popularity {p['popularity']}/100, genres: {genres_str}, "
            f"active since {p['active_since']}, {p['total_releases']} releases"
        )

    benchmark_section = ""
    if benchmark_peers:
        benchmark_section = "BENCHMARK PEERS — Similar artists who have already broken through:\n"
        benchmark_section += "\n".join(_fmt_peer(p) for p in benchmark_peers)
        benchmark_section += "\n"

    prompt = f"""You are a senior music industry strategist with 20 years of experience in artist development, A&R, and growth marketing. You have worked with artists across all career stages and genres.

ARTIST INTELLIGENCE DATA:
- Name: {artist_name}
- Genres: {', '.join(genres) or 'Unknown'}
- Last.fm Tags: {', '.join(lastfm_tags[:15]) or 'N/A'}
- Spotify Followers: {followers:,}
- Spotify Popularity Score: {popularity}/100
- Last.fm Weekly Listeners: {lastfm_stats.get('listeners', 'N/A')}
- Last.fm Total Scrobbles: {lastfm_stats.get('scrobbles', 'N/A')}
- Total Releases: {release_stats_obj.get('total_releases', 'N/A')}
- Active Since: {release_stats_obj.get('first_release_year', 'Unknown')}
- Record Labels: {', '.join(artist_labels) or 'Independent'}
- Origin / Area: {mb_area or 'Unknown'}
- Market Presence: {', '.join(f'{k} ({v} countries)' for k, v in market_breakdown.items()) or 'Unknown'}

{benchmark_section}
CRITICAL RULES — your strategy MUST:
1. Name SPECIFIC real services, platforms, tools, and publications (e.g. SubmitHub, Groover, Grooveshark, AWAL, Amuse, NME, Pitchfork, KEXP, The Line of Best Fit, etc.) — never generic placeholders.
2. Give CONCRETE numbers where relevant (e.g. "submit to 15–20 playlist curators per release", "aim for 3 TikTok posts/week", "target labels with rosters under 30 artists").
3. Reference REAL named artist examples to illustrate comparisons (e.g. "similar trajectory to Mitski before ANTI- signing" or "comparable positioning to Amyl and the Sniffers in the punk space").
4. Every action must be immediately doable — no vague advice like "grow your social following".
5. Do NOT use filler phrases like "unique sound", "authentic connection", or "sonic journey".

Return ONLY a valid JSON object. No markdown. No explanation. Exactly this schema:
{{
  "positioning": "Precise 2-sentence market positioning statement using the actual genre tags and data.",
  "career_stage": "Underground | Emerging | Rising | Established | Mainstream",
  "target_audience": {{
    "primary": "Specific demographic + behavioral description (age, platforms, listen depth)",
    "secondary": "Secondary audience segment",
    "psychographic": "Concrete values, lifestyle markers, and listening contexts (commute, gym, late night, etc.)",
    "top_platforms": ["Platform1", "Platform2", "Platform3"]
  }},
  "geographic_strategy": {{
    "priority_markets": ["Country1", "Country2", "Country3"],
    "reasoning": "Data-backed reasoning for each market choice",
    "expansion_markets": ["Country1", "Country2"],
    "approach": "Concrete geographic expansion steps — e.g. target specific regional blogs, book specific cities, use Spotify geo-targeted ads"
  }},
  "playlist_strategy": {{
    "target_tier": "micro (< 5K followers) | mid (5K–50K) | major (50K+) | all tiers",
    "submission_services": ["SubmitHub", "Groover", "Submithub Editorial", "Playlistsupply"],
    "genres_to_pitch": ["genre1", "genre2", "genre3"],
    "mood_keywords": ["mood1", "mood2", "mood3"],
    "curator_profile": "Specific type of curator to target with account size range and subject matter",
    "pitch_tip": "Concrete pitch tip: what to include in the message, how long, what angle to lead with for this genre"
  }},
  "press_targets": [
    {{"outlet": "Real publication name", "section": "Specific section/column", "why": "Concrete reason it fits"}},
    {{"outlet": "Real publication name", "section": "Specific section/column", "why": "Concrete reason it fits"}},
    {{"outlet": "Real publication name", "section": "Specific section/column", "why": "Concrete reason it fits"}},
    {{"outlet": "Real publication name", "section": "Specific section/column", "why": "Concrete reason it fits"}},
    {{"outlet": "Real publication name", "section": "Specific section/column", "why": "Concrete reason it fits"}}
  ],
  "social_strategy": {{
    "primary_platform": "Most important platform for this specific genre/demographic",
    "why": "Concrete data-backed reason",
    "content_pillars": ["Specific pillar with format", "Specific pillar with format", "Specific pillar with format"],
    "posting_cadence": "Specific frequency per platform (e.g. TikTok: 4x/week, Instagram: 1x/day Reels + 3x Stories)",
    "growth_tactic": "Specific tactic with named technique — e.g. 'Duet with 3 mid-tier creators in the [genre] niche weekly', name specific hashtag clusters",
    "tools": ["Buffer", "Later", "CapCut", "or other relevant tool"]
  }},
  "sync_licensing": {{
    "viability": "High | Medium | Low — with brief reason",
    "target_placements": ["TV show genre/tone", "Ad category", "Film genre"],
    "libraries_to_submit": ["Musicbed", "Artlist", "Epidemic Sound", "Pond5", "or genre-appropriate library"],
    "sync_agent_tip": "Specific advice on approaching sync agents or supervisors for this genre"
  }},
  "label_strategy": {{
    "recommendation": "Stay indie | Approach indie labels | Approach major labels | Distribution deal only",
    "reasoning": "Metric-backed reasoning (e.g. 'at 25K followers and 60 popularity, you have leverage for a favorable indie deal')",
    "target_labels": ["Specific real label name 1", "Specific real label name 2", "Specific real label name 3"],
    "approach": "Concrete pitch approach — e.g. 'cold email A&R at [Label] with last 3 months streaming stats showing X% growth, attach EPK with press quotes'"
  }},
  "collaboration_strategy": {{
    "collab_type": "Feature | Joint EP | Tour | Co-write | Remix Exchange",
    "target_artist_tier": "Specific follower/popularity range to target",
    "named_examples": ["Real artist 1 at similar level", "Real artist 2 at similar level"],
    "genre_adjacency": "Which adjacent genres offer biggest cross-promo opportunity",
    "approach": "Concrete outreach method — DM, email, mutual booking agent, etc."
  }},
  "ninety_day_plan": [
    {{"week": "1–2", "focus": "Specific focus area", "actions": ["Concrete action with named tool/platform", "Concrete action", "Concrete action"]}},
    {{"week": "3–4", "focus": "Specific focus area", "actions": ["Concrete action", "Concrete action", "Concrete action"]}},
    {{"week": "5–8", "focus": "Specific focus area", "actions": ["Concrete action", "Concrete action"]}},
    {{"week": "9–12", "focus": "Specific focus area", "actions": ["Concrete action", "Concrete action"]}}
  ],
  "quick_wins": [
    "Specific actionable win doable this week (name the tool/platform/contact)",
    "Specific actionable win doable this week",
    "Specific actionable win doable this week",
    "Specific actionable win doable this week"
  ],
  "benchmark_analysis": {{
    "peer_comparison": "1–2 sentences comparing target artist directly to the benchmark peers using specific metric gaps (e.g. 'X has 4× fewer followers than [Peer] despite a similar active-since year').",
    "gap_to_close": "The 2–3 most critical measurable gaps between target and benchmark peers.",
    "timeline_estimate": "Realistic timeline to reach the next tier based on benchmark peer trajectories — cite specific peer names."
  }},
  "success_playbook": [
    {{
      "artist": "One of the real benchmark peer names from the data above",
      "snapshot": "Where they were ~2–3 years into their career: followers, labels, market presence",
      "breakthrough": "The single most important move that accelerated their career — be specific (e.g. 'landed editorial playlist X', 'signed to Y label', 'went viral on TikTok via Z content format')",
      "lesson": "The direct, actionable lesson for {artist_name}: what to copy from this playbook right now"
    }},
    {{
      "artist": "Another benchmark peer name",
      "snapshot": "Where they were at a comparable stage",
      "breakthrough": "Their breakthrough move",
      "lesson": "Direct actionable lesson for {artist_name}"
    }},
    {{
      "artist": "A third benchmark peer or a well-known artist with a similar trajectory even if not in the list",
      "snapshot": "Where they were at a comparable stage",
      "breakthrough": "Their breakthrough move",
      "lesson": "Direct actionable lesson for {artist_name}"
    }}
  ]
}}"""

    try:
        import re as re_module
        text = _ai_call(prompt)
        # Strip any markdown code fences
        text = re_module.sub(r'^```(?:json)?\s*', '', text)
        text = re_module.sub(r'\s*```$', '', text)
        import json as json_module
        strategy = json_module.loads(text)
        return jsonify({'strategy': strategy, 'benchmark_peers': benchmark_peers})
    except Exception as e:
        print(f"[MarketingAPI] Claude/parse error: {e}")
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
