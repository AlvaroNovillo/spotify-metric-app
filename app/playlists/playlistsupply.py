import requests
import json
import traceback
from bs4 import BeautifulSoup
import time # Added for potential delays

# --- login_to_playlistsupply function ---
def login_to_playlistsupply(username, password):
    """
    Attempts to log in to PlaylistSupply using provided credentials.
    *** WARNING: Highly unstable, insecure, and likely violates ToS. ***
    Returns an authenticated requests.Session object or None on failure.
    """
    print("[Login PS] Attempting to log in to PlaylistSupply...")
    if not username or not password:
        print("[Login PS] Error: Username and password are required.")
        return None

    login_url = "https://playlistsupply.com/amember/login"
    # Use a persistent session object
    session = requests.Session()

    # Set headers to mimic a browser
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36', # Example UA
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://playlistsupply.com/amember/login', # Referer often checked
        'Origin': 'https://playlistsupply.com',             # Origin often checked
        'DNT': '1', # Do Not Track
        'Upgrade-Insecure-Requests': '1',
        'Cache-Control': 'max-age=0',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        # Add more headers if inspection reveals them
    })

    # Payload for the login form
    login_data = {
        'amember_login': username,
        'amember_pass': password,
        'login_attempt_id': str(int(time.time())), # May need dynamic value if present
        # Check for other hidden fields like '_csrf_token' if they exist
    }

    try:
        # Optional: First GET request to potentially fetch cookies or hidden form fields
        # print(f"[Login PS] Sending initial GET request to {login_url}")
        # initial_resp = session.get(login_url, timeout=15)
        # initial_resp.raise_for_status()
        # print(f"[Login PS] Initial GET status: {initial_resp.status_code}")
        # Parse initial_resp.text with BeautifulSoup to find hidden fields if necessary

        # Send the POST request to log in
        print(f"[Login PS] Sending POST request to {login_url}")
        response = session.post(login_url, data=login_data, allow_redirects=True, timeout=20) # Follow redirects
        response.raise_for_status() # Raise exception for 4xx/5xx errors

        print(f"[Login PS] Response status after POST: {response.status_code}")
        final_url = response.url
        print(f"[Login PS] Response URL after POST: {final_url}")

        # Check if login was successful
        # A common failure is being redirected back to the login page
        if login_url in final_url:
            # Analyze the response HTML to see *why* it failed (e.g., error message)
            soup = BeautifulSoup(response.text, 'html.parser')
            # Look for common error message containers
            error_elements = soup.select('.am-errors li, div.error, .alert-danger')
            if error_elements:
                error_msg = ', '.join([e.get_text(strip=True) for e in error_elements])
                print(f"[Login PS] Login failed. Still on login page. Found error message(s): {error_msg}")
            else:
                print("[Login PS] Login failed. Still on login page, but no specific error message found (maybe CAPTCHA, incorrect creds, or changed layout?).")
            return None # Login failed

        # Another check: Look for elements that ONLY appear when logged in (e.g., logout button, dashboard link)
        # soup = BeautifulSoup(response.text, 'html.parser')
        # logout_link = soup.find('a', href=lambda href: href and 'logout' in href)
        # if not logout_link:
        #     print("[Login PS] Login likely failed: Could not find expected logged-in element (e.g., logout link) on the final page.")
        #     return None # Login likely failed

        # If not redirected back to login and no specific error found, assume success
        print("[Login PS] Success: Redirected away from login page or login page content indicates success. Assuming login.")
        return session # Return the authenticated session object

    except requests.exceptions.Timeout:
        print("[Login PS] Error: Login request timed out.")
        return None
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
    Attempts to scrape playlist results using a pre-authenticated session.
    Handles potential HTML warnings prepended to JSON and checks for session expiry.

    Args:
        search_term (str): The keyword to search for.
        user_email (str): The email associated with the PlaylistSupply account (needed for API param).
        authenticated_session (requests.Session): The session object returned by successful login.

    Returns:
        list: A list of playlist dictionaries on success.
        dict: An error dictionary {'error': '...', 'message': '...'} on specific failures.
        None: On critical request errors (e.g., timeout).

    *** WARNING: Highly unstable and relies on reverse-engineering. Likely violates ToS. ***
    """
    print(f"--- [Scraper PS] Attempting to scrape PlaylistSupply for: '{search_term}' ---")
    if not authenticated_session:
        print("[Scraper PS] Error: Invalid or missing authenticated session provided.")
        return {"error": "session_invalid", "message": "Authentication session is missing or invalid."}
    if not user_email:
        print("[Scraper PS] Error: User email is required for scraping parameters.")
        # This is a config error in our app if it happens
        return {"error": "config_error", "message": "User email (required for scraper param) is missing."}
    if not search_term:
         print("[Scraper PS] Warning: Empty search term provided.")
         return [] # Return empty list for empty search term


    # Target endpoint identified from network analysis ( liable to change!)
    # Ensure URL encoding for parameters, especially the keyword
    params = {
        'user_email': user_email,
        'code': 'email', # This parameter seems constant based on observation
        'keyword': search_term
    }
    target_endpoint = "https://playlistsupply.com/tool/timemachine_reloaded.php"

    print(f"[Scraper PS] Targeting endpoint: {target_endpoint}")
    print(f"[Scraper PS] Using parameters: {params}")
    # print(f"[Scraper PS] Using session cookies: {authenticated_session.cookies.get_dict()}") # Debugging

    extracted_playlists = []
    response = None # Initialize response to None
    try:
        # Make the GET request using the authenticated session
        response = authenticated_session.get(target_endpoint, params=params, timeout=45) # Increased timeout
        response.raise_for_status() # Check for 4xx/5xx errors

        print(f"[Scraper PS] Request status: {response.status_code}")
        content_type = response.headers.get('Content-Type', '').lower()
        print(f"[Scraper PS] Response content type: {content_type}")
        response_text = response.text

        # --- Robust Parsing Logic ---
        json_data = None
        parse_error = None

        try:
            # Attempt 1: Direct JSON parsing (if content type looks right or as default)
            # PlaylistSupply might send JSON with text/html content-type sometimes
            json_data = json.loads(response_text)
            print("[Scraper PS] Parsed response directly as JSON.")

        except json.JSONDecodeError as json_err_direct:
            # Attempt 2: Handle potential HTML prepended to JSON
            print(f"[Scraper PS] Direct JSON parse failed ({json_err_direct}). Checking for embedded JSON...")
            # Find the first '{' or '[' that likely starts the JSON data
            json_start_index = -1
            first_brace = response_text.find('{')
            first_bracket = response_text.find('[')

            # Prioritize '[' if it appears before '{' or if '{' is not found
            if first_bracket != -1 and (first_brace == -1 or first_bracket < first_brace):
                json_start_index = first_bracket
            elif first_brace != -1:
                json_start_index = first_brace

            if json_start_index != -1:
                print(f"[Scraper PS] Found potential JSON start at index {json_start_index}.")
                json_substring = response_text[json_start_index:]
                try:
                    json_data = json.loads(json_substring)
                    print("[Scraper PS] Successfully parsed JSON substring after potential HTML prefix.")
                except json.JSONDecodeError as json_err_inner:
                    print(f"[Scraper PS] Error: Failed to decode JSON substring: {json_err_inner}")
                    parse_error = json_err_inner # Store the error
                    # Log snippet for debugging
                    # print("[Scraper PS] Response Text Snippet:", response_text[:500])
            else:
                print("[Scraper PS] Error: Could not find JSON start character '[' or '{' in non-JSON response.")
                parse_error = ValueError("JSON start marker not found") # Set specific error

        # --- Process if json_data was successfully loaded ---
        if json_data is not None:
            # PlaylistSupply seems to return a list of playlist dicts
            if not isinstance(json_data, list):
                print(f"[Scraper PS] Warning: Expected JSON list, but got {type(json_data)}. Structure may have changed.")
                # Depending on structure, maybe try processing anyway, or return empty
                return [] # Return empty list on unexpected structure for safety

            print(f"[Scraper PS] Processing {len(json_data)} items from JSON.")
            for playlist_data in json_data:
                if not isinstance(playlist_data, dict):
                    # print(f"[Scraper PS]   Skipping non-dictionary item: {playlist}")
                    continue

                # Extract relevant fields, providing defaults
                playlist = {
                    'id': playlist_data.get('id'), # Assuming 'id' is the Spotify playlist ID
                    'name': playlist_data.get('name', 'N/A'),
                    'url': playlist_data.get('url'), # Should be the Spotify URL
                    'description': playlist_data.get('description', ''),
                    'tracks_total': playlist_data.get('tracks_total', 'N/A'),
                    'followers': playlist_data.get('followers', 'N/A'), # Often a string like '1,234' or '5.6k'
                    'email': playlist_data.get('email'), # Curator email if found
                    'owner_name': playlist_data.get('owner_name', 'N/A'), # Curator name
                    'owner_url': playlist_data.get('owner_url'), # Link to curator profile
                    'last_modified': playlist_data.get('last_modified', 'unknown') # When PS last saw update?
                }

                # Basic validation: Ensure it looks like a Spotify playlist URL
                if playlist.get('url') and 'open.spotify.com/playlist/' in playlist['url']:
                    extracted_playlists.append(playlist)
                # else: print(f"[Scraper PS]   Skipping item with invalid/missing Spotify URL: {playlist.get('name', 'Unknown Name')}")

            print(f"--- [Scraper PS] Finished processing. Found {len(extracted_playlists)} valid playlists for '{search_term}'. ---")
            return extracted_playlists # <<< SUCCESS CASE

        # --- Handle cases where JSON parsing failed ---
        else:
            print("[Scraper PS] JSON data could not be parsed from the response.")
            # Check if the response looks like the login page (indicating session expired)
            soup = BeautifulSoup(response_text, 'html.parser')
            login_form = soup.find('form', {'name': 'login'}) # Check for login form specifically
            login_input = soup.find('input', {'name': 'amember_login'})
            if login_form and login_input:
                print("[Scraper PS] Detected PlaylistSupply login form elements. Session likely expired or invalid.")
                return {"error": "session_invalid", "message": "PlaylistSupply session expired or became invalid."}
            else:
                # Got non-JSON, non-login-form HTML. Could be an error page or unexpected content.
                print("[Scraper PS] Received non-JSON, non-login HTML content. Treating as empty result or error.")
                # Log a snippet for debugging
                # print("[Scraper PS] Response Text Snippet:", response_text[:500])
                # You might return an error dict here too if this state is unexpected
                # return {"error": "unexpected_content", "message": "Received unexpected HTML content from PlaylistSupply."}
                return [] # Treat as empty result for now

    except requests.exceptions.Timeout:
        print(f"[Scraper PS] Error: Request to PlaylistSupply timed out for '{search_term}'.")
        return None # Indicate critical request failure
    except requests.exceptions.RequestException as e:
        print(f"[Scraper PS] Error during request to {target_endpoint} for '{search_term}': {e}")
        if response is not None and (response.status_code == 401 or response.status_code == 403):
            print("[Scraper PS] Authorization error (401/403). Session likely invalid.")
            return {"error": "session_invalid", "message": f"Authorization error ({response.status_code}) accessing PlaylistSupply."}
        # For other request errors (DNS, connection), return None
        return None
    except Exception as e:
        print(f"[Scraper PS] Unexpected error during scraping for '{search_term}': {e}")
        traceback.print_exc()
        return {"error": "unexpected_error", "message": f"An unexpected error occurred during scraping: {e}"}