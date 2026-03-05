import requests
import traceback
import urllib.parse

WP_REST_BASE = "https://en.wikipedia.org/api/rest_v1"

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'FuzzTracks/1.0 (contact@fuzztracks.com)',
    'Accept': 'application/json',
})


def _get_summary_by_title(title):
    """Fetch Wikipedia page summary by title slug."""
    encoded = urllib.parse.quote(title, safe='')
    url = f"{WP_REST_BASE}/page/summary/{encoded}"
    try:
        resp = SESSION.get(url, timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[Wikipedia] Error fetching summary for '{title}': {e}")
        return None


def _get_summary_by_search(artist_name):
    """Search Wikipedia for an artist and return the best summary."""
    search_url = "https://en.wikipedia.org/w/api.php"
    params = {
        'action': 'query',
        'list': 'search',
        'srsearch': artist_name,
        'srlimit': 3,
        'format': 'json',
    }
    try:
        resp = SESSION.get(search_url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results = data.get('query', {}).get('search', [])
        if not results:
            return None
        # Take first result
        title = results[0]['title']
        return _get_summary_by_title(title)
    except Exception as e:
        print(f"[Wikipedia] Search error for '{artist_name}': {e}")
        return None


def get_artist_summary(artist_name, wikipedia_url=None):
    """
    Fetch a Wikipedia summary for an artist.
    Tries the provided URL first, then falls back to search.

    Returns a dict with:
        - title: Page title
        - extract: Short bio text
        - thumbnail_url: Image URL (or None)
        - page_url: Full Wikipedia URL
    """
    summary_data = None

    # Try via URL from MusicBrainz first
    if wikipedia_url and 'wikipedia.org/wiki/' in wikipedia_url:
        title = wikipedia_url.split('/wiki/')[-1]
        summary_data = _get_summary_by_title(title)
        if summary_data:
            print(f"[Wikipedia] Got summary via MusicBrainz URL for '{artist_name}'")

    # Fallback: search by name
    if not summary_data:
        summary_data = _get_summary_by_search(artist_name)
        if summary_data:
            print(f"[Wikipedia] Got summary via search for '{artist_name}'")

    if not summary_data:
        print(f"[Wikipedia] No summary found for '{artist_name}'")
        return None

    extract = summary_data.get('extract', '')
    # Truncate to ~500 chars for display
    if len(extract) > 600:
        extract = extract[:597] + '...'

    thumbnail = summary_data.get('thumbnail', {})
    thumbnail_url = thumbnail.get('source') if thumbnail else None

    page_url = summary_data.get('content_urls', {}).get('desktop', {}).get('page')

    return {
        'title': summary_data.get('title'),
        'extract': extract,
        'thumbnail_url': thumbnail_url,
        'page_url': page_url,
    }
