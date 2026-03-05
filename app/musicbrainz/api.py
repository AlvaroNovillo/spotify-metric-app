import requests
import time
import traceback

MB_BASE = "https://musicbrainz.org/ws/2"

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'FuzzTracks/1.0 (contact@fuzztracks.com)',
    'Accept': 'application/json',
})

_last_request_time = 0


def _mb_get(endpoint, params=None):
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)

    url = f"{MB_BASE}/{endpoint}"
    if params is None:
        params = {}
    params['fmt'] = 'json'

    try:
        resp = SESSION.get(url, params=params, timeout=15)
        _last_request_time = time.time()
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        print(f"[MusicBrainz] HTTP error for {endpoint}: {e}")
        return None
    except Exception as e:
        print(f"[MusicBrainz] Error fetching {endpoint}: {e}")
        return None


def find_artist_mbid(artist_name):
    """Search MusicBrainz for an artist and return their MBID."""
    data = _mb_get('artist', {'query': f'artist:"{artist_name}"', 'limit': 5})
    if not data or not data.get('artists'):
        return None

    artists = data['artists']
    name_lower = artist_name.lower()

    # Prefer exact name match
    for artist in artists:
        if artist.get('name', '').lower() == name_lower:
            return artist.get('id')

    # Fall back to first result with score >= 90
    for artist in artists:
        if int(artist.get('score', 0)) >= 90:
            return artist.get('id')

    return artists[0].get('id') if artists else None


# Platform name detection from URL
_SOCIAL_DOMAINS = {
    'instagram.com': 'Instagram',
    'twitter.com': 'Twitter / X',
    'x.com': 'Twitter / X',
    'facebook.com': 'Facebook',
    'tiktok.com': 'TikTok',
    'youtube.com': 'YouTube',
    'youtu.be': 'YouTube',
    'soundcloud.com': 'SoundCloud',
    'bandcamp.com': 'Bandcamp',
    'open.spotify.com': 'Spotify',
    'music.apple.com': 'Apple Music',
    'vk.com': 'VKontakte',
    'snapchat.com': 'Snapchat',
    'pinterest.com': 'Pinterest',
    'tumblr.com': 'Tumblr',
    'discord.gg': 'Discord',
    'discord.com': 'Discord',
    'threads.net': 'Threads',
    'linktr.ee': 'Linktree',
    'linktree.com': 'Linktree',
}

_PLATFORM_ICONS = {
    'Instagram': 'fab fa-instagram',
    'Twitter / X': 'fab fa-x-twitter',
    'Facebook': 'fab fa-facebook',
    'TikTok': 'fab fa-tiktok',
    'YouTube': 'fab fa-youtube',
    'SoundCloud': 'fab fa-soundcloud',
    'Bandcamp': 'fab fa-bandcamp',
    'Spotify': 'fab fa-spotify',
    'Apple Music': 'fab fa-apple',
    'VKontakte': 'fab fa-vk',
    'Discord': 'fab fa-discord',
    'Snapchat': 'fab fa-snapchat',
    'Pinterest': 'fab fa-pinterest',
    'Threads': 'fab fa-threads',
    'Linktree': 'fas fa-link',
}


def _detect_platform(url):
    url_lower = url.lower()
    for domain, name in _SOCIAL_DOMAINS.items():
        if domain in url_lower:
            return name, _PLATFORM_ICONS.get(name, 'fas fa-link')
    return None, 'fas fa-link'


def get_artist_intel(mbid):
    """
    Fetch comprehensive artist data from MusicBrainz:
    - URL relationships (website, social, Wikipedia, etc.)
    - Label relationships
    - Artist relationships (management, etc.)
    """
    if not mbid:
        return None

    data = _mb_get(
        f'artist/{mbid}',
        {'inc': 'url-rels+label-rels+artist-rels+aliases+genres+tags'}
    )
    if not data:
        return None

    result = {
        'mbid': mbid,
        'name': data.get('name'),
        'sort_name': data.get('sort-name'),
        'disambiguation': data.get('disambiguation'),
        'type': data.get('type'),
        'country': data.get('country'),
        'area': data.get('area', {}).get('name') if data.get('area') else None,
        'begin_area': data.get('begin-area', {}).get('name') if data.get('begin-area') else None,
        'life_span': data.get('life-span', {}),
        'mb_genres': [g.get('name') for g in data.get('genres', []) if g.get('name')],
        'website': None,
        'wikipedia_url': None,
        'wikipedia_title': None,
        'social_links': [],
        'streaming_links': [],
        'labels': [],
        'management': [],
        'other_links': [],
    }

    relations = data.get('relations', [])

    for rel in relations:
        target_type = rel.get('target-type', '')
        rel_type = rel.get('type', '').lower()

        if target_type == 'url':
            url_obj = rel.get('url', {})
            url = url_obj.get('resource', '') if url_obj else ''
            if not url:
                continue

            if rel_type == 'official homepage':
                result['website'] = url

            elif rel_type in ('wikipedia', 'wikidata'):
                result['wikipedia_url'] = url
                # Extract page title from URL
                if 'wikipedia.org/wiki/' in url:
                    result['wikipedia_title'] = url.split('/wiki/')[-1]

            elif rel_type in ('social network', 'streaming music', 'free streaming', 'video channel', 'music service'):
                platform, icon = _detect_platform(url)
                if platform in ('YouTube', 'SoundCloud', 'Bandcamp', 'Spotify', 'Apple Music'):
                    result['streaming_links'].append({'platform': platform, 'icon': icon, 'url': url})
                elif platform:
                    result['social_links'].append({'platform': platform, 'icon': icon, 'url': url})
                else:
                    result['other_links'].append({'label': rel_type.title(), 'url': url})

            elif rel_type in ('image', 'fanpage', 'purchase for download', 'download for free',
                              'get the music', 'lyrics', 'interview', 'review', 'discogs', 'allmusic'):
                # Skip or store as other
                pass

            else:
                platform, icon = _detect_platform(url)
                if platform:
                    result['social_links'].append({'platform': platform, 'icon': icon, 'url': url})

        elif target_type == 'label':
            label = rel.get('label', {})
            if label and label.get('name'):
                # Avoid duplicates
                names = [l['name'] for l in result['labels']]
                if label['name'] not in names:
                    result['labels'].append({
                        'name': label.get('name'),
                        'mbid': label.get('id'),
                        'type': label.get('type'),
                        'begin': rel.get('begin'),
                        'end': rel.get('end'),
                        'ended': rel.get('ended', False),
                    })

        elif target_type == 'artist':
            if rel_type in ('management', 'manager', 'booking agent', 'agent',
                            'business management', 'artistry producer'):
                artist_rel = rel.get('artist', {})
                if artist_rel and artist_rel.get('name'):
                    result['management'].append({
                        'name': artist_rel.get('name'),
                        'role': rel.get('type', 'Manager').title(),
                        'mbid': artist_rel.get('id'),
                        'begin': rel.get('begin'),
                        'end': rel.get('end'),
                    })

    # Deduplicate social links by platform
    seen_platforms = set()
    unique_social = []
    for link in result['social_links']:
        key = link['platform']
        if key not in seen_platforms:
            seen_platforms.add(key)
            unique_social.append(link)
    result['social_links'] = unique_social

    seen_platforms = set()
    unique_streaming = []
    for link in result['streaming_links']:
        key = link['platform']
        if key not in seen_platforms:
            seen_platforms.add(key)
            unique_streaming.append(link)
    result['streaming_links'] = unique_streaming

    # Sort labels: active ones first
    result['labels'].sort(key=lambda l: (l['ended'], -(int(l['begin']) if l['begin'] and l['begin'].isdigit() else 0)))

    print(f"[MusicBrainz] Extracted data for MBID {mbid}: "
          f"{len(result['social_links'])} social, {len(result['labels'])} labels, "
          f"{len(result['management'])} management entries")

    return result


def get_label_website(label_mbid):
    """Fetch the official website for a label from MusicBrainz."""
    if not label_mbid:
        return None
    data = _mb_get(f'label/{label_mbid}', {'inc': 'url-rels'})
    if not data:
        return None
    for rel in data.get('relations', []):
        if rel.get('target-type') == 'url' and rel.get('type', '').lower() == 'official homepage':
            url_obj = rel.get('url', {})
            return url_obj.get('resource') if url_obj else None
    return None


def get_label_contacts(label_name):
    """
    Search MusicBrainz for a label by name and return contact information:
    official website, social media links, country, and type.
    Returns a dict or None if not found.
    """
    if not label_name:
        return None

    data = _mb_get('label', {'query': f'label:"{label_name}"', 'limit': 5})
    if not data or not data.get('labels'):
        return None

    labels = data['labels']
    name_lower = label_name.lower()

    best = None
    for label in labels:
        if label.get('name', '').lower() == name_lower:
            best = label
            break
    if not best:
        for label in labels:
            if int(label.get('score', 0)) >= 85:
                best = label
                break
    if not best and labels:
        best = labels[0]

    if not best:
        return None

    mbid = best.get('id')
    if not mbid:
        return None

    full = _mb_get(f'label/{mbid}', {'inc': 'url-rels'})
    if not full:
        return None

    result = {
        'mbid': mbid,
        'name': full.get('name', label_name),
        'type': full.get('type'),
        'country': full.get('country'),
        'website': None,
        'social_links': [],
        'other_urls': [],
    }

    for rel in full.get('relations', []):
        if rel.get('target-type') != 'url':
            continue
        url = rel.get('url', {}).get('resource', '')
        if not url:
            continue
        rel_type = rel.get('type', '').lower()

        if rel_type == 'official homepage':
            result['website'] = url
        elif rel_type in ('social network', 'streaming music', 'free streaming',
                          'video channel', 'music service'):
            platform, icon = _detect_platform(url)
            if platform:
                result['social_links'].append({'platform': platform, 'icon': icon, 'url': url})
            else:
                result['other_urls'].append({'label': rel_type.title(), 'url': url})
        else:
            platform, icon = _detect_platform(url)
            if platform:
                result['social_links'].append({'platform': platform, 'icon': icon, 'url': url})

    print(f"[MusicBrainz] Label '{label_name}': website={bool(result['website'])}, "
          f"social={len(result['social_links'])}")
    return result
