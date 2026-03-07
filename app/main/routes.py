import time
import traceback
import math
import pandas as pd
import io
import requests
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


def _generate_ai_bio(artist, lastfm_tags, lastfm_stats, mb_data, wiki_extract, artist_labels, release_stats, audio_averages=None):
    """Use Gemini to synthesize all available data into a professional artist profile."""
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

        audio_section = 'N/A'
        if audio_averages:
            _lbl = {
                'energy':           ['very low energy','low energy','moderate energy','high energy','very high energy'],
                'danceability':     ['not danceable','slightly danceable','moderately danceable','danceable','highly danceable'],
                'valence':          ['dark/melancholic','somewhat dark','neutral mood','upbeat','euphoric'],
                'acousticness':     ['heavily electronic','mostly electronic','mixed','mostly acoustic','fully acoustic'],
                'instrumentalness': ['vocal-led','mostly vocal','balanced','mostly instrumental','fully instrumental'],
            }
            lines = []
            for k in ['energy','danceability','valence','acousticness','instrumentalness']:
                v = audio_averages.get(k)
                if v is not None:
                    lbl = _lbl[k][min(int(v * 5), 4)]
                    lines.append(f"  {k.title()}: {v} ({lbl})")
            if 'tempo' in audio_averages:
                lines.append(f"  Tempo: {audio_averages['tempo']} BPM")
            audio_section = '\n'.join(lines) if lines else 'N/A'

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
Audio DNA (avg of top tracks):
{audio_section}

Return exactly this JSON structure (all strings, use real data — NO filler phrases like "unique blend"):
{{
  "genre_profile": "2–3 sentence description of their exact genre positioning, sub-genres, sonic characteristics and key influences — cite the Audio DNA numbers where available (e.g. 'Energy 0.72 + Acousticness 0.18 signals a high-energy electronic lean').",
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


def _parse_intent(intent: str, artist_name: str) -> dict:
    """
    Fast Gemini call to extract structured intent from the user's natural language goal.
    Returns a dict with intent_types, release_name, release_type, is_upcoming,
    reference_artists, target_markets, timeframe, key_context.
    Gracefully returns {} on any failure.
    """
    if not intent or len(intent) < 5:
        return {}
    try:
        import google.generativeai as genai
        from flask import current_app
        api_key = current_app.config.get('GEMINI_API_KEY')
        model_name = current_app.config.get('GEMINI_MODEL_NAME')
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        prompt = (
            f'Artist: {artist_name}\n'
            f'Goal statement: "{intent}"\n\n'
            'Extract structured intent. Return ONLY a raw JSON object, no markdown, no extra text:\n'
            '{\n'
            '  "intent_types": ["release_promo","audience_growth","sync_licensing","touring",'
            '"label_search","social_growth","collaboration","press_coverage"],\n'
            '  "release_name": "specific release title if named, else null",\n'
            '  "release_type": "ep" or "album" or "single" or null,\n'
            '  "is_upcoming": true if the release is upcoming/unreleased,\n'
            '  "reference_artists": ["max 3 artist names mentioned for sonic comparison"],\n'
            '  "target_markets": ["max 4 country or region names explicitly mentioned"],\n'
            '  "timeframe": "immediate" or "short_term" or "long_term",\n'
            '  "key_context": "one sentence summarising the core ask"\n'
            '}\n'
            'Rules: intent_types must be a list of only the applicable types from the enum. '
            'reference_artists are ONLY artists explicitly named as sonic references ("sounds like X", "in the vein of X"). '
            'target_markets are ONLY geographic places explicitly mentioned.'
        )
        import re as _re, json as _json
        resp = model.generate_content(prompt)
        text = (resp.text or '').strip()
        m = _re.search(r'\{.*\}', text, _re.DOTALL)
        if m:
            return _json.loads(m.group())
    except Exception as e:
        print(f'[IntentParse] Error: {e}')
    return {}


def _fetch_reference_artist_context(sp, names: list) -> str:
    """
    Given a list of artist names (from the user's intent, e.g. 'sounds like Dominic Fike'),
    look each one up on Spotify, get their audio DNA via ReccoBeats, and return a
    formatted context string for the Gemini prompt.
    """
    if not names:
        return ''
    lines = []
    for name in names[:3]:
        try:
            res = sp.search(q=name, type='artist', limit=1)
            items = (res.get('artists') or {}).get('items') or []
            if not items:
                continue
            a = items[0]
            a_name = a.get('name', name)
            a_pop = a.get('popularity', 0)
            a_followers = (a.get('followers') or {}).get('total', 0)
            a_genres = a.get('genres', [])[:3]

            tops = sp.artist_top_tracks(a['id'], country='US').get('tracks', [])[:5]
            t_ids = [t['id'] for t in tops if t.get('id')]
            a_audio = {}
            if t_ids:
                rb = requests.get(
                    'https://api.reccobeats.com/v1/audio-features',
                    params={'ids': ','.join(t_ids)},
                    timeout=8,
                )
                valid_f = [f for f in rb.json().get('content', []) if f]
                if valid_f:
                    for key in ['danceability', 'energy', 'valence', 'acousticness', 'instrumentalness']:
                        a_audio[key] = round(sum(f.get(key, 0) for f in valid_f) / len(valid_f), 2)
                    a_audio['tempo'] = round(sum(f.get('tempo', 0) for f in valid_f) / len(valid_f))

            f_str = (f'{a_followers/1_000_000:.1f}M' if a_followers >= 1_000_000
                     else f'{a_followers/1_000:.0f}K' if a_followers >= 1_000
                     else str(a_followers))
            line = (f'  - {a_name}: {f_str} followers, popularity {a_pop}/100'
                    f', genres: {", ".join(a_genres) or "N/A"}')
            if a_audio:
                line += (
                    f'\n    Sonic profile: energy {a_audio.get("energy","?")} | '
                    f'danceability {a_audio.get("danceability","?")} | '
                    f'valence {a_audio.get("valence","?")} | '
                    f'acousticness {a_audio.get("acousticness","?")} | '
                    f'tempo {a_audio.get("tempo","?")} bpm'
                )
            lines.append(line)
        except Exception as e:
            print(f'[ReferenceArtist] Error for "{name}": {e}')

    if not lines:
        return ''
    return (
        '\nSOUND REFERENCE ARTISTS (named by artist as sonic benchmarks):\n'
        + '\n'.join(lines)
        + '\n→ Use these artists\' audio DNA numbers to calibrate playlist targeting, sync fit, '
        'and positioning language. The target artist\'s sound MUST be described relative to these '
        'benchmarks — cite specific metric differences (e.g. "higher energy than Dominic Fike\'s 0.62").\n'
    )


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

    # ── Audio features via ReccoBeats ────────────────────────────────────
    audio_averages = {}
    try:
        sp_top = sp.artist_top_tracks(artist_id, country='US').get('tracks', [])[:5]
        track_ids = [t['id'] for t in sp_top if t.get('id')]
        if track_ids:
            rb = requests.get(
                'https://api.reccobeats.com/v1/audio-features',
                params={'ids': ','.join(track_ids)},
                timeout=10,
            )
            rb.raise_for_status()
            valid_f = [f for f in rb.json().get('content', []) if f]
            if valid_f:
                for key in ['danceability', 'energy', 'valence', 'acousticness', 'instrumentalness']:
                    audio_averages[key] = round(sum(f.get(key, 0) for f in valid_f) / len(valid_f), 2)
                audio_averages['tempo'] = round(sum(f.get('tempo', 0) for f in valid_f) / len(valid_f))
    except Exception as e:
        print(f"[IntelAPI] Audio features error: {e}")
    result['audio_averages'] = audio_averages

    try:
        result['ai_bio'] = _generate_ai_bio(
            artist, result['lastfm_tags'], result['lastfm_stats'],
            result['musicbrainz'], wiki_extract, artist_labels, release_stats,
            audio_averages=audio_averages,
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


@main_bp.route('/api/artist/<artist_id>/marketing-strategy', methods=['GET', 'POST'])
def marketing_strategy_api(artist_id):
    """Generate a full AI marketing strategy using Gemini."""
    sp = get_spotify_client_credentials_client()
    if not sp:
        return jsonify({'error': 'Spotify unavailable'}), 503

    # Accept intent from POST body or query string
    intent = ''
    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        intent = (body.get('intent') or '').strip()[:1500]
    else:
        intent = (request.args.get('intent') or '').strip()[:1500]

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
    mb_wiki_url = None
    try:
        from ..musicbrainz.api import find_artist_mbid, get_artist_intel
        mbid = find_artist_mbid(artist_name)
        if mbid:
            mb = get_artist_intel(mbid)
            if mb:
                mb_area = mb.get('area') or mb.get('begin_area')
                mb_wiki_url = mb.get('wikipedia_url')
    except Exception:
        pass

    wiki_extract = None
    try:
        from ..wikipedia.api import get_artist_summary
        wiki_data = get_artist_summary(artist_name, mb_wiki_url)
        if wiki_data:
            wiki_extract = (wiki_data.get('extract') or '')[:600]
    except Exception:
        pass

    market_breakdown = _compute_market_breakdown(available_markets)

    # ── Phase 1: NLP intent parsing ───────────────────────────────────────
    parsed_intent = {}
    if intent:
        try:
            parsed_intent = _parse_intent(intent, artist_name)
            print(f"[MarketingAPI] Parsed intent: {parsed_intent}")
        except Exception as e:
            print(f"[MarketingAPI] Intent parse failed: {e}")

    p_intent_types  = parsed_intent.get('intent_types') or []
    p_release_name  = (parsed_intent.get('release_name') or '').strip()
    p_release_type  = (parsed_intent.get('release_type') or '').lower()
    p_is_upcoming   = bool(parsed_intent.get('is_upcoming', False))
    p_ref_artists   = parsed_intent.get('reference_artists') or []
    p_target_mkts   = parsed_intent.get('target_markets') or []
    p_timeframe     = parsed_intent.get('timeframe') or 'short_term'
    p_key_context   = (parsed_intent.get('key_context') or '').strip()

    # ── Top tracks + audio features ──────────────────────────────────────
    # For sync_licensing intent, fetch more tracks for a broader sonic picture
    top_track_limit = 10 if 'sync_licensing' in p_intent_types else 5
    top_tracks_data = []
    audio_averages = {}
    try:
        sp_top = sp.artist_top_tracks(artist_id, country='US').get('tracks', [])[:top_track_limit]
        top_tracks_data = [
            {'name': t.get('name', ''), 'popularity': t.get('popularity', 0),
             'year': (t.get('album', {}).get('release_date', '') or '')[:4]}
            for t in sp_top
        ]
        track_ids = [t['id'] for t in sp_top if t.get('id')]
        if track_ids:
            rb_resp = requests.get(
                'https://api.reccobeats.com/v1/audio-features',
                params={'ids': ','.join(track_ids)},
                timeout=10,
            )
            rb_resp.raise_for_status()
            features_list = rb_resp.json().get('content', [])
            valid_f = [f for f in features_list if f]
            if valid_f:
                for key in ['danceability', 'energy', 'valence', 'acousticness', 'instrumentalness']:
                    audio_averages[key] = round(sum(f.get(key, 0) for f in valid_f) / len(valid_f), 2)
                audio_averages['tempo'] = round(sum(f.get('tempo', 0) for f in valid_f) / len(valid_f))
    except Exception as e:
        print(f"[MarketingAPI] Audio features error: {e}")

    # ── Phase 2a: Release context (driven by parsed intent) ───────────────
    release_context_section = ""
    needs_release = (
        intent and (
            'release_promo' in p_intent_types
            or p_release_name
            or p_release_type
            or any(kw in intent.lower() for kw in ['ep', 'album', 'single', 'track', 'release'])
        )
    )
    if needs_release:
        try:
            all_releases = sp.artist_albums(
                artist_id, album_type='album,single', limit=20, country='US'
            ).get('items', [])

            matched_rel = None
            _intent_lower = intent.lower()

            # 1) Exact name match from NLP-extracted release_name
            if p_release_name:
                for rel in all_releases:
                    if p_release_name.lower() in rel.get('name', '').lower():
                        matched_rel = rel
                        break

            # 2) Partial name match: scan all release names against intent text
            if not matched_rel:
                for rel in all_releases:
                    rname = rel.get('name', '').lower()
                    if rname and len(rname) >= 3 and rname in _intent_lower:
                        matched_rel = rel
                        break

            # 3) Type-based fallback using NLP release_type or keyword
            if not matched_rel and all_releases:
                rtype = p_release_type or ('ep' if 'ep' in _intent_lower
                                           else 'album' if 'album' in _intent_lower else '')
                if rtype:
                    matched_rel = next(
                        (r for r in all_releases if r.get('album_type', '').lower() == rtype),
                        all_releases[0]
                    )
                else:
                    matched_rel = all_releases[0]

            if matched_rel:
                full_rel = sp.album(matched_rel['id'])
                rel_tracks = full_rel.get('tracks', {}).get('items', [])[:12]
                rel_track_ids = [t['id'] for t in rel_tracks if t.get('id')]

                rel_feat_map = {}
                rel_audio_avgs = {}
                if rel_track_ids:
                    rb2 = requests.get(
                        'https://api.reccobeats.com/v1/audio-features',
                        params={'ids': ','.join(rel_track_ids)},
                        timeout=10,
                    )
                    rb2.raise_for_status()
                    rb2_content = rb2.json().get('content', [])
                    valid_rel_f = [f for f in rb2_content if f]
                    for j, feat in enumerate(rb2_content):
                        if feat and j < len(rel_track_ids):
                            rel_feat_map[rel_track_ids[j]] = feat
                    if valid_rel_f:
                        for key in ['danceability', 'energy', 'valence', 'acousticness', 'instrumentalness']:
                            rel_audio_avgs[key] = round(
                                sum(f.get(key, 0) for f in valid_rel_f) / len(valid_rel_f), 2
                            )
                        rel_audio_avgs['tempo'] = round(
                            sum(f.get('tempo', 0) for f in valid_rel_f) / len(valid_rel_f)
                        )

                track_detail_lines = []
                for t in rel_tracks:
                    tid = t.get('id', '')
                    tname = t.get('name', '')
                    f = rel_feat_map.get(tid)
                    if f:
                        track_detail_lines.append(
                            f"    • \"{tname}\" — "
                            f"energy:{f.get('energy', 0):.2f}  "
                            f"dance:{f.get('danceability', 0):.2f}  "
                            f"valence:{f.get('valence', 0):.2f}  "
                            f"acousticness:{f.get('acousticness', 0):.2f}  "
                            f"tempo:{f.get('tempo', 0):.0f} bpm"
                        )
                    else:
                        track_detail_lines.append(f"    • \"{tname}\"")

                rel_audio_str = ""
                if rel_audio_avgs:
                    rel_audio_str = (
                        f"\n  Avg Audio Profile: energy {rel_audio_avgs.get('energy','N/A')} | "
                        f"danceability {rel_audio_avgs.get('danceability','N/A')} | "
                        f"valence {rel_audio_avgs.get('valence','N/A')} | "
                        f"acousticness {rel_audio_avgs.get('acousticness','N/A')} | "
                        f"tempo {rel_audio_avgs.get('tempo','N/A')} bpm"
                    )

                rel_type_str = full_rel.get('album_type', 'release').upper()
                rel_name_str = full_rel.get('name', '')
                rel_date     = full_rel.get('release_date', 'TBA')
                rel_label    = full_rel.get('label', 'Independent')
                rel_markets  = len(full_rel.get('available_markets', []))
                rel_pop      = full_rel.get('popularity', 0)
                is_upcoming  = p_is_upcoming or any(
                    kw in intent.lower()
                    for kw in ['new', 'upcoming', 'unreleased', 'dropping', 'releasing', 'about to']
                )

                release_context_section = (
                    f"\nTARGET RELEASE — \"{rel_name_str}\" ({rel_type_str}):\n"
                    f"  Release Date: {rel_date}"
                    f"{'  [UPCOMING — build full pre-release rollout]' if is_upcoming else ''}\n"
                    f"  Label: {rel_label}\n"
                    f"  Available in {rel_markets} Spotify markets  |  Popularity: {rel_pop}/100\n"
                    f"  Tracklist ({len(rel_tracks)} tracks):\n"
                    f"{chr(10).join(track_detail_lines)}"
                    f"{rel_audio_str}\n"
                    f"→ Reference this release by name in EVERY section of the strategy. "
                    f"Use per-track audio features to assign specific tracks to playlists, sync cues, "
                    f"and social content moments. "
                    f"{'Build a staged pre-release rollout in the 90-day plan.' if is_upcoming else ''}\n"
                )
        except Exception as e:
            print(f"[MarketingAPI] Release context error: {e}")

    # ── Phase 2b: Reference artist lookup (sonic benchmarks from intent) ──
    ref_artists_section = ""
    if p_ref_artists:
        try:
            ref_artists_section = _fetch_reference_artist_context(sp, p_ref_artists)
        except Exception as e:
            print(f"[MarketingAPI] Reference artist error: {e}")

    # ── Related artists (shared pool for benchmarks + collabs) ───────────
    _related_all = []
    try:
        _related_all = sp.artist_related_artists(artist_id).get('artists', [])
    except Exception as e:
        print(f"[MarketingAPI] Related artists error: {e}")

    # ── Collaboration targets (similar-level artists) ─────────────────────
    collab_targets = []
    try:
        _collab_pool = [
            a for a in _related_all
            if a.get('popularity', 0) > 0
            and (a.get('followers', {}).get('total', 0) or 0) < max(followers, 1) * 2.5
            and a.get('popularity', 0) < popularity + 10
        ]
        _collab_pool.sort(key=lambda x: abs((x.get('followers', {}).get('total', 0) or 0) - followers))
        collab_targets = [
            {'name': a.get('name', ''),
             'followers': (a.get('followers', {}).get('total', 0) or 0),
             'genres': a.get('genres', [])[:2]}
            for a in _collab_pool[:5]
        ]
    except Exception as e:
        print(f"[MarketingAPI] Collab targets error: {e}")

    # ── Benchmark peers: similar artists with stronger metrics ──────────
    benchmark_peers = []
    try:
        candidates = [
            a for a in _related_all
            if (a.get('popularity', 0) >= popularity + 8
                or (a.get('followers', {}).get('total', 0) or 0) >= max(followers, 1) * 2)
            and a.get('popularity', 0) > 0
        ]
        candidates.sort(
            key=lambda x: (x.get('followers', {}).get('total', 0) or 0),
            reverse=True
        )
        from ..spotify.utils import calculate_release_stats as _crs
        from ..musicbrainz.api import find_artist_mbid as _mb_find, get_artist_intel as _mb_intel
        for peer in candidates[:5]:
            peer_id = peer['id']
            peer_followers = (peer.get('followers', {}).get('total', 0) or 0)
            peer_name = peer.get('name', '')
            peer_genres = peer.get('genres', [])
            peer_popularity = peer.get('popularity', 0)
            peer_stats = {}
            peer_top_track = None
            peer_top_track_pop = 0
            peer_manager = None
            peer_booking_agent = None
            peer_mb_labels = []
            try:
                peer_releases = sp.artist_albums(peer_id, album_type='album,single', limit=10).get('items', [])
                peer_stats = _crs(peer_releases) or {}
            except Exception:
                pass
            try:
                top_tracks = sp.artist_top_tracks(peer_id).get('tracks', [])
                if top_tracks:
                    top = max(top_tracks, key=lambda t: t.get('popularity', 0))
                    peer_top_track = top.get('name')
                    peer_top_track_pop = top.get('popularity', 0)
            except Exception:
                pass
            # MB lookup only for top 2 peers (1.1s rate limit per call)
            if len(benchmark_peers) < 2:
                try:
                    peer_mbid = _mb_find(peer_name)
                    if peer_mbid:
                        peer_mb = _mb_intel(peer_mbid)
                        if peer_mb:
                            mgmt = peer_mb.get('management', [])
                            for m in mgmt:
                                role = m.get('role', '').lower()
                                if 'booking' in role or 'agent' in role:
                                    peer_booking_agent = m['name']
                                elif 'manag' in role:
                                    peer_manager = m['name']
                            peer_mb_labels = [l['name'] for l in peer_mb.get('labels', []) if not l.get('ended')][:2]
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
                'top_track': peer_top_track,
                'top_track_popularity': peer_top_track_pop,
                'manager': peer_manager,
                'booking_agent': peer_booking_agent,
                'mb_labels': peer_mb_labels,
            })
    except Exception as e:
        print(f"[MarketingAPI] Benchmark peers error: {e}")

    def _audio_label(key, val):
        thresholds = {
            'energy':           ['very low energy', 'low energy', 'moderate energy', 'high energy', 'very high energy'],
            'danceability':     ['not danceable', 'slightly danceable', 'moderately danceable', 'danceable', 'highly danceable'],
            'valence':          ['dark/melancholic', 'somewhat dark', 'neutral mood', 'upbeat', 'euphoric/happy'],
            'acousticness':     ['heavily electronic', 'mostly electronic', 'mixed acoustic/electronic', 'mostly acoustic', 'fully acoustic'],
            'instrumentalness': ['vocal-dominated', 'mostly vocal', 'balanced', 'mostly instrumental', 'fully instrumental'],
        }
        idx = min(int(val * 5), 4)
        return thresholds.get(key, [''] * 5)[idx]

    tracks_section = "\n".join(
        f'  {i+1}. "{t["name"]}" — popularity {t["popularity"]}/100 ({t["year"] or "N/A"})'
        for i, t in enumerate(top_tracks_data)
    ) if top_tracks_data else "  N/A"

    audio_lines = [
        f"  - {k.title()}: {audio_averages[k]} → {_audio_label(k, audio_averages[k])}"
        for k in ['energy', 'danceability', 'valence', 'acousticness', 'instrumentalness']
        if k in audio_averages
    ]
    if 'tempo' in audio_averages:
        audio_lines.append(f"  - Tempo: {audio_averages['tempo']} BPM")
    audio_section = "\n".join(audio_lines) if audio_lines else "  N/A"

    engagement_ratio_str = "N/A"
    try:
        _scr = int(lastfm_stats.get('scrobbles') or 0)
        _lst = int(lastfm_stats.get('listeners') or 0)
        if _lst > 0:
            _r = round(_scr / _lst, 1)
            if _r >= 50:   _elbl = "very high repeat listening — dedicated cult fanbase"
            elif _r >= 20: _elbl = "high repeat listening — loyal core audience"
            elif _r >= 8:  _elbl = "moderate engagement — growing but mixed listener depth"
            else:          _elbl = "low repeat rate — mostly passive/discovery listeners"
            engagement_ratio_str = f"{_r} ({_elbl})"
    except Exception:
        pass

    collab_section = "\n".join(
        "  - {name}: {f_str} followers, genres: {genres}".format(
            name=c['name'],
            f_str=f"{c['followers']/1_000_000:.1f}M" if c['followers'] >= 1_000_000
                  else f"{c['followers']/1_000:.0f}K" if c['followers'] >= 1_000
                  else str(c['followers']),
            genres=', '.join(c['genres']) or 'similar genre'
        ) for c in collab_targets
    ) if collab_targets else "  N/A"

    wiki_section = wiki_extract[:500] if wiki_extract else "Not available"

    def _fmt_peer(p):
        genres_str = ', '.join(p['genres']) if p['genres'] else 'similar genre'
        f = p['followers']
        f_str = f"{f/1_000_000:.1f}M" if f >= 1_000_000 else f"{f/1_000:.0f}K" if f >= 1_000 else str(f)
        line = (
            f"  - {p['name']}: {f_str} followers ({p['followers_multiplier']}× target), "
            f"popularity {p['popularity']}/100, genres: {genres_str}, "
            f"active since {p['active_since']}, {p['total_releases']} releases"
        )
        if p.get('top_track'):
            line += f", biggest track: \"{p['top_track']}\" (popularity {p['top_track_popularity']}/100)"
        if p.get('manager'):
            line += f", manager: {p['manager']}"
        if p.get('booking_agent'):
            line += f", booking agent: {p['booking_agent']}"
        if p.get('mb_labels'):
            line += f", labels: {', '.join(p['mb_labels'])}"
        return line

    benchmark_section = ""
    if benchmark_peers:
        benchmark_section = "BENCHMARK PEERS — Similar artists who have already broken through:\n"
        benchmark_section += "\n".join(_fmt_peer(p) for p in benchmark_peers)
        benchmark_section += "\n"

    if intent:
        _intent_meta_lines = [f'"{intent}"']
        if p_key_context:
            _intent_meta_lines.append(f'Core ask: {p_key_context}')
        if p_intent_types:
            _intent_meta_lines.append(f'Primary objectives: {", ".join(p_intent_types)}')
        if p_timeframe:
            _intent_meta_lines.append(
                f'Timeframe: {p_timeframe.replace("_", " ")} '
                f'({"< 2 weeks" if p_timeframe == "immediate" else "1–3 months" if p_timeframe == "short_term" else "3+ months"})'
            )
        if p_target_mkts:
            _intent_meta_lines.append(f'Target markets explicitly mentioned: {", ".join(p_target_mkts)}')
        if release_context_section:
            _intent_meta_lines.append('A specific release has been identified below — reference it by name throughout every section.')
        if p_ref_artists:
            _intent_meta_lines.append(
                f'Sonic reference artists (mentioned by artist): {", ".join(p_ref_artists)} — '
                'their audio profiles are provided below; use them to calibrate all recommendations.'
            )
        intent_block = (
            'ARTIST GOAL / RELEASE INTENT:\n'
            + '\n'.join(_intent_meta_lines) + '\n'
            + '→ Every section of your strategy MUST directly serve this goal.\n'
            + '→ intent_actions must give the 5 most specific, immediately actionable steps for exactly this goal.\n\n'
        )
    else:
        intent_block = ""

    prompt = f"""You are a senior music industry strategist with 20 years of experience in artist development, A&R, and growth marketing. You have worked with artists across all career stages and genres.

{intent_block}ARTIST INTELLIGENCE DATA:
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
- Top Markets: {', '.join(available_markets[:10]) or 'Unknown'}

TOP TRACKS (Spotify):
{tracks_section}

AUDIO DNA (average of top {len(top_tracks_data)} tracks):
{audio_section}

AUDIENCE ENGAGEMENT:
- Scrobble/Listener ratio: {engagement_ratio_str}

COLLABORATION OPPORTUNITIES (similar-level artists to target):
{collab_section}

ARTIST BACKGROUND (Wikipedia):
{wiki_section}
{ref_artists_section}{release_context_section}
{benchmark_section}
CRITICAL RULES — your strategy MUST:
1. Name SPECIFIC real services, platforms, tools, and publications (e.g. SubmitHub, Groover, AWAL, Amuse, NME, Pitchfork, KEXP, The Line of Best Fit, etc.) — never generic placeholders.
2. Give CONCRETE numbers where relevant (e.g. "submit to 15–20 playlist curators per release", "aim for 3 TikTok posts/week", "target labels with rosters under 30 artists").
3. Reference REAL named artist examples to illustrate comparisons (e.g. "similar trajectory to Mitski before ANTI- signing" or "comparable positioning to Amyl and the Sniffers in the punk space").
4. Every action must be immediately doable — no vague advice like "grow your social following".
5. Do NOT use filler phrases like "unique sound", "authentic connection", or "sonic journey".
6. Reference the artist's ACTUAL top track names (listed above) in social strategy, content hooks, and quick wins — never say "your music" generically.
7. Use the AUDIO DNA numbers to justify every playlist mood keyword, sync placement, and energy profile — cite the actual metric values (e.g. "energy 0.82 signals high-intensity workout playlists").
8. The collaboration_strategy named_examples MUST use artists from the Collaboration Opportunities list above — these are real, similarly-sized artists in their ecosystem.
{f"9. SOUND REFERENCE ARTISTS are provided above — always position the target artist relative to them with specific metric comparisons (e.g. 'higher valence than Dominic Fike\\'s 0.41'). These are the artist\\'s own stated inspirations." if ref_artists_section else ""}

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
    "named_examples": ["Artist from the Collaboration Opportunities list above — name exactly", "Second artist from the list"],
    "outreach_angle": "Specific opening angle for each named artist — what shared ground to reference (genre overlap, shared label space, similar audience)",
    "genre_adjacency": "Which adjacent genre from the data offers biggest cross-promo opportunity and why",
    "approach": "Concrete outreach method — DM, email, mutual booking agent, sync supervisor connection, etc."
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
  "audio_strategy": {{
    "mood_profile": "1-sentence characterization of the artist's sound using the Audio DNA numbers — state the exact values and what editorial tier/context they signal (e.g. 'Energy 0.82 + Valence 0.28 = dark, high-intensity electronic suited for late-night and workout editorial')",
    "top_playlists_to_target": [
      "Specific named playlist archetype + mood/energy justification from the Audio DNA data",
      "Second specific playlist archetype",
      "Third specific playlist archetype"
    ],
    "standout_track": "Name the top track from the list above, cite its popularity score, and explain why it should lead all pitches (mood fit, recency, streaming momentum)",
    "sync_fit": {{
      "tv_drama": "High/Medium/Low — cite the specific audio metric that justifies this",
      "ad_campaigns": "High/Medium/Low — cite the specific audio metric",
      "sports_fitness": "High/Medium/Low — cite the specific audio metric"
    }}
  }},
  "benchmark_analysis": {{
    "peer_comparison": "1–2 sentences comparing target artist directly to the benchmark peers using specific metric gaps (e.g. 'X has 4× fewer followers than [Peer] despite a similar active-since year').",
    "gap_to_close": "The 2–3 most critical measurable gaps between target and benchmark peers.",
    "timeline_estimate": "Realistic timeline to reach the next tier based on benchmark peer trajectories — cite specific peer names."
  }},
  "success_playbook": [
    {{
      "artist": "One of the real benchmark peer names from the data above",
      "snapshot": "Where they were ~2–3 years into their career: followers, labels, market presence in 1–2 sentences",
      "viral_hit": "Their most important song or moment — name the exact track/video, when it happened, and what drove it (TikTok trend, editorial playlist add, TV sync, radio campaign, etc.)",
      "manager_agency": "Their management company or manager name — use the data provided; fill gaps from your own knowledge. If truly unknown write 'Unknown'.",
      "booking_agency": "Their booking or touring agency — use data provided; fill gaps from your own knowledge. If truly unknown write 'Unknown'.",
      "breakthrough": "The single most important strategic move that accelerated their career — be specific: name labels, platforms, curators, or campaigns",
      "lesson": "The direct, actionable lesson for {artist_name} distilled in one sentence",
      "action_steps": [
        "Concrete step {artist_name} can take this month that mirrors this case — name the tool, service, or contact",
        "Second concrete step — specific and measurable",
        "Third concrete step — specific and measurable"
      ]
    }},
    {{
      "artist": "Another benchmark peer name",
      "snapshot": "Where they were at a comparable stage",
      "viral_hit": "Their key viral or breakout track/moment",
      "manager_agency": "Their management company or 'Unknown'",
      "booking_agency": "Their booking agency or 'Unknown'",
      "breakthrough": "Their breakthrough strategic move",
      "lesson": "Direct actionable lesson for {artist_name}",
      "action_steps": ["Step 1", "Step 2", "Step 3"]
    }},
    {{
      "artist": "A third benchmark peer or a well-known artist with a similar trajectory even if not in the list",
      "snapshot": "Where they were at a comparable stage",
      "viral_hit": "Their key viral or breakout track/moment",
      "manager_agency": "Their management company or 'Unknown'",
      "booking_agency": "Their booking agency or 'Unknown'",
      "breakthrough": "Their breakthrough strategic move",
      "lesson": "Direct actionable lesson for {artist_name}",
      "action_steps": ["Step 1", "Step 2", "Step 3"]
    }}
  ],
  "intent_actions": {{
    "goal": "{intent if intent else 'The primary goal most likely to move the needle for this artist right now, based on career stage and data.'}",
    "priority_focus": "The single most critical strategic area to focus on to achieve this goal — one of: playlist_strategy / press / social / sync / label / collaboration / geographic",
    "immediate_steps": [
      "Step 1 — actionable this week, name the exact tool, platform, or person to contact",
      "Step 2 — specific and measurable",
      "Step 3",
      "Step 4",
      "Step 5"
    ],
    "success_metric": "The one KPI or milestone that proves progress toward this goal (e.g. '3 editorial playlist adds', '5 press features', '10K new followers')",
    "timeline": "Realistic timeframe to achieve this goal given the artist's current metrics"
  }}
}}"""

    try:
        import re as re_module
        text = _ai_call(prompt)
        # Strip any markdown code fences
        text = re_module.sub(r'^```(?:json)?\s*', '', text)
        text = re_module.sub(r'\s*```$', '', text)
        import json as json_module
        strategy = json_module.loads(text)
        return jsonify({'strategy': strategy, 'benchmark_peers': benchmark_peers, 'intent': intent})
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
