# --- START OF (REVISED) FILE app/playlists/playlistsupply.py ---
import requests
import json
import traceback
from bs4 import BeautifulSoup
import time
import re # Import the regular expressions module

# ... (login_to_playlistsupply function remains unchanged) ...
def login_to_playlistsupply(username, password):
    # ... (no changes here)
    print("[Login PS] Attempting to log in to PlaylistSupply...")
    if not username or not password:
        print("[Login PS] Error: Username and password are required.")
        return None

    login_url = "https://playlistsupply.com/amember/login"
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'en-US,en;q=0.9', 'Referer': 'https://playlistsupply.com/amember/login',
        'Origin': 'https://playlistsupply.com', 'DNT': '1', 'Upgrade-Insecure-Requests': '1',
        'Cache-Control': 'max-age=0', 'Sec-Fetch-Dest': 'document', 'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-User': '?1',
    })
    login_data = {'amember_login': username, 'amember_pass': password, 'login_attempt_id': str(int(time.time()))}
    try:
        response = session.post(login_url, data=login_data, allow_redirects=True, timeout=20)
        response.raise_for_status()
        final_url = response.url
        if login_url in final_url:
            soup = BeautifulSoup(response.text, 'html.parser')
            error_elements = soup.select('.am-errors li, div.error, .alert-danger')
            if error_elements:
                error_msg = ', '.join([e.get_text(strip=True) for e in error_elements])
                print(f"[Login PS] Login failed. Found error message(s): {error_msg}")
            else:
                print("[Login PS] Login failed. Still on login page.")
            return None
        print("[Login PS] Success: Assuming login.")
        return session
    except requests.exceptions.RequestException as e:
        print(f"[Login PS] Error during login request: {e}")
        return None
    except Exception as e:
        print(f"[Login PS] Unexpected error during login: {e}")
        traceback.print_exc()
        return None

# --- MODIFIED PlaylistSupply Scraping Function ---
def scrape_playlistsupply(search_term, user_email, authenticated_session):
    """
    Attempts to scrape playlist results and extracts the REAL Spotify ID from the URL.
    """
    print(f"--- [Scraper PS] Attempting to scrape PlaylistSupply for: '{search_term}' ---")
    if not authenticated_session:
        return {"error": "session_invalid", "message": "Authentication session is missing or invalid."}
    if not user_email:
        return {"error": "config_error", "message": "User email (required for scraper param) is missing."}
    if not search_term:
         return []

    params = {'user_email': user_email, 'code': 'email', 'keyword': search_term}
    # FIX: Updated URL to include /libs/
    target_endpoint = "https://playlistsupply.com/tool/libs/timemachine_reloaded.php"
    extracted_playlists = []
    response = None
    try:
        # FIX: Added headers to mimic AJAX request
        headers = {
            'Referer': 'https://playlistsupply.com/tool/search.php',
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': '*/*',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin'
        }
        response = authenticated_session.get(target_endpoint, params=params, headers=headers, timeout=45)
        response.raise_for_status()
        response_text = response.text
        json_data = None
        try:
            json_data = json.loads(response_text)
        except json.JSONDecodeError:
            json_start_index = response_text.find('[') # PlaylistSupply usually returns a list
            if json_start_index != -1:
                json_substring = response_text[json_start_index:]
                try: json_data = json.loads(json_substring)
                except json.JSONDecodeError as e: print(f"[Scraper PS] Error: Failed to decode JSON substring: {e}");
            else: print("[Scraper PS] Error: Could not find JSON start in non-JSON response.")

        if json_data is not None:
            if not isinstance(json_data, list):
                print(f"[Scraper PS] Warning: Expected JSON list, but got {type(json_data)}.")
                return []
            
            for playlist_data in json_data:
                if not isinstance(playlist_data, dict): continue

                playlist_url = playlist_data.get('url')
                spotify_id = None

                # --- Extract the REAL Spotify ID from the URL ---
                if playlist_url and 'open.spotify.com/playlist/' in playlist_url:
                    match = re.search(r'playlist/([a-zA-Z0-9]+)', playlist_url)
                    if match:
                        spotify_id = match.group(1)
                
                # If we couldn't get a valid Spotify ID, skip this playlist entirely
                if not spotify_id:
                    continue

                playlist = {
                    # --- CRITICAL: Use the extracted Spotify ID as the main 'id' ---
                    'id': spotify_id,
                    'name': playlist_data.get('name', 'N/A'),
                    'url': playlist_url,
                    'description': playlist_data.get('description', ''),
                    'tracks_total': playlist_data.get('tracks_total', 'N/A'),
                    'followers': playlist_data.get('followers', 'N/A'),
                    'email': playlist_data.get('email'),
                    'owner_name': playlist_data.get('owner_name', 'N/A'),
                    'owner_url': playlist_data.get('owner_url'),
                    'last_modified': playlist_data.get('last_modified', 'unknown')
                }
                extracted_playlists.append(playlist)
            
            print(f"--- [Scraper PS] Finished. Found {len(extracted_playlists)} valid playlists for '{search_term}'. ---")
            return extracted_playlists
        else:
            soup = BeautifulSoup(response_text, 'html.parser')
            if soup.find('form', {'name': 'login'}):
                return {"error": "session_invalid", "message": "PlaylistSupply session expired."}
            return []

    except requests.exceptions.RequestException as e:
        print(f"[Scraper PS] Error during request for '{search_term}': {e}")
        if response is not None and (response.status_code in [401, 403]):
            return {"error": "session_invalid", "message": f"Authorization error ({response.status_code})."}
        return None
    except Exception as e:
        print(f"[Scraper PS] Unexpected error scraping for '{search_term}': {e}")
        traceback.print_exc()
        return {"error": "unexpected_error", "message": f"An unexpected error occurred: {e}"}