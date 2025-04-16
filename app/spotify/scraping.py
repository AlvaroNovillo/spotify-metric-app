# --- START OF FILE app/spotify/scraping.py ---
import requests
import json
import time
import traceback
import urllib.parse

# Use a persistent session for requests
SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36', # Example UA
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'application/json', # Expecting JSON
    'App-Platform': 'WebPlayer', # Mimic web player
})

ANONYMOUS_TOKEN_CACHE = {
    'accessToken': None,
    'clientToken': None,
    'expiresAt': 0
}

def _get_anonymous_spotify_token():
    """
    Attempts to fetch an anonymous access token. Likely to fail now.
    Returns: tuple: (access_token, client_token) or (None, None) on failure.
    """
    now = int(time.time())
    if ANONYMOUS_TOKEN_CACHE['accessToken'] and ANONYMOUS_TOKEN_CACHE['expiresAt'] > now + 60:
        print("[Token Fetcher] Using cached anonymous token.")
        return ANONYMOUS_TOKEN_CACHE['accessToken'], ANONYMOUS_TOKEN_CACHE['clientToken']

    print("[Token Fetcher] Attempting to fetch new anonymous token (EXPECTED TO FAIL)...")
    # This URL is likely blocked or changed
    token_url = "https://open.spotify.com/get_access_token?reason=transport&productType=web-player"
    try:
        headers = SESSION.headers.copy(); headers.update({'Accept': 'application/json'})
        response = SESSION.get(token_url, headers=headers, timeout=10)
        response.raise_for_status() # This will likely raise the 400 error
        data = response.json()
        access_token = data.get('accessToken'); client_token = data.get('clientId'); expires_ms = data.get('accessTokenExpirationTimestampMs')
        if not all([access_token, client_token, expires_ms]): raise ValueError("Incomplete token data")
        ANONYMOUS_TOKEN_CACHE['accessToken'] = access_token; ANONYMOUS_TOKEN_CACHE['clientToken'] = client_token
        ANONYMOUS_TOKEN_CACHE['expiresAt'] = expires_ms // 1000
        print("[Token Fetcher] Successfully fetched anonymous token (UNEXPECTED).")
        return access_token, client_token
    except requests.exceptions.RequestException as e:
        print(f"[Token Fetcher] Network error fetching anonymous token: {e}") # Log the actual error
        return None, None
    except Exception as e:
        print(f"[Token Fetcher] Error processing anonymous token response: {e}")
        # traceback.print_exc()
        return None, None


def fetch_related_artists_via_internal_api(artist_id):
    """
    Attempts to fetch related artists via internal API. Likely to fail due to token issues.
    Returns list of basic artist dicts, empty list, or None.
    """
    # ... (function logic as before - it will likely return None due to token failure) ...
    if not artist_id: return []
    print(f"[Internal API Fetch] Attempting related artists for {artist_id} via internal API...")
    access_token, client_token = _get_anonymous_spotify_token()
    if not access_token or not client_token: print("[Internal API Fetch] Failed to get anonymous tokens."); return None

    operation_name="queryArtistRelated"; persisted_query_hash="3d031d6cb22a2aa7c8d203d49b49df731f58b1e2799cc38d9876d58771aa66f3"
    variables=json.dumps({"uri":f"spotify:artist:{artist_id}"})
    extensions=json.dumps({"persistedQuery":{"version":1,"sha256Hash":persisted_query_hash}})
    params={"operationName": operation_name,"variables": variables,"extensions": extensions}; encoded_params=urllib.parse.urlencode(params)
    api_url=f"https://api-partner.spotify.com/pathfinder/v1/query?{encoded_params}"
    headers=SESSION.headers.copy(); headers.update({'Authorization': f'Bearer {access_token}','Client-Token': client_token,'Accept': 'application/json','App-Platform': 'WebPlayer','Spotify-App-Version': '1.2.62.509.g7eb4151e'}) # Example version

    response = None
    try:
        print(f"[Internal API Fetch] Making GET request to Pathfinder API..."); response = SESSION.get(api_url, headers=headers, timeout=15); response.raise_for_status(); data = response.json()
        related_artists_data = data.get('data', {}).get('artistUnion', {}).get('relatedContent', {}).get('relatedArtists', {}).get('items', [])
        if not related_artists_data: print("[Internal API Fetch] No related artists found in API response."); return []
        extracted_artists = []
        for item in related_artists_data:
             if not isinstance(item, dict): continue
             artist_id = item.get('id'); artist_name = item.get('profile', {}).get('name'); artist_uri = item.get('uri'); images = item.get('visuals', {}).get('avatarImage', {}).get('sources', [])
             image_url = None
             if images: small_images = [img['url'] for img in images if img.get('width') and img['width'] <= 160]; image_url = small_images[0] if small_images else images[0].get('url')
             if artist_id and artist_name: extracted_artists.append({'id': artist_id, 'name': artist_name, 'uri': artist_uri, 'images': [{'url': image_url}] if image_url else []})
        print(f"[Internal API Fetch] Successfully extracted {len(extracted_artists)} related artists."); return extracted_artists
    except requests.exceptions.RequestException as e: print(f"[Internal API Fetch] Network error querying Pathfinder API: {e}"); print(f" Status: {response.status_code if response else 'N/A'}, Text: {response.text[:200] if response else 'N/A'}"); return None
    except Exception as e: print(f"[Internal API Fetch] Error processing Pathfinder response: {e}"); traceback.print_exc(); return []

# --- END OF FILE app/spotify/scraping.py ---