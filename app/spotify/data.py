# --- START OF FILE app/spotify/data.py ---
import time
import traceback
from flask import session # Still potentially useful for session clearing on critical errors
import spotipy
# Import the CORRECT function name for getting the client credentials client
from .auth import get_spotify_client_credentials_client


def fetch_similar_genre_artists_data(sp_client, artist_id, artist_name, artist_genres, limit=5):
    """
    Fetches a list of artists based on genre similarity to the main artist.

    Args:
        sp_client: Authenticated spotipy client instance (using client credentials).
        artist_id (str): Spotify ID of the main artist.
        artist_name (str): Name of the main artist.
        artist_genres (list): List of genres for the main artist.
        limit (int): The maximum number of similar artists to return.

    Returns:
        list: A list of simplified artist dictionaries (id, name, genres, etc.)
              sorted by popularity, or an empty list on error/no results.
    """
    # Function body remains the same...
    if not sp_client:
        print("[Similar Fetch] Error: Invalid Spotify client provided.")
        return []
    if not artist_id or not artist_genres:
        print("[Similar Fetch] Missing artist ID or genres.")
        return []

    # Use top 2 genres for seeding recommendations or searching
    genres_to_search = artist_genres[:2]
    print(f"[Similar Fetch] Searching for artists similar to '{artist_name}' based on genres: {', '.join(genres_to_search)}")

    similar_artists = {}
    max_results_per_genre = 20 # Fetch more initially to get better variety

    try:
        # Fallback/Simpler method: Search by genre
        print("[Similar Fetch] Using genre search method.")
        for genre in genres_to_search:
            query = f'genre:"{genre}"'
            print(f"[Similar Fetch]   Searching: {query}")
            # Increase limit for search to get more candidates
            results = sp_client.search(q=query, type='artist', limit=max_results_per_genre)

            if results and results['artists'] and results['artists']['items']:
                for artist in results['artists']['items']:
                    # Exclude the main artist and avoid duplicates
                    if artist['id'] != artist_id and artist['id'] not in similar_artists:
                        # Store the full artist object temporarily or just needed fields
                        similar_artists[artist['id']] = artist # Store full object for now
            time.sleep(0.1) # Be nice to the API

        # Sort collected artists by popularity
        found_artists_list = sorted(
            list(similar_artists.values()),
            key=lambda a: a.get('popularity', 0),
            reverse=True
        )

        print(f"[Similar Fetch] Found {len(found_artists_list)} potential similar artists, returning top {limit}.")
        return found_artists_list[:limit] # Return the top 'limit' artists

    except spotipy.exceptions.SpotifyException as e:
        print(f"[Similar Fetch] Spotify API error: {e}")
        # Client credentials errors might indicate wrong ID/Secret
        if e.http_status == 401 or e.http_status == 403:
            print("-> Authorization error during similar artist fetch. Check Client ID/Secret.")
            # No session to clear here
        return []
    except Exception as e:
        print(f"[Similar Fetch] Unexpected error: {e}")
        traceback.print_exc()
        return []


def fetch_release_details(sp_client, releases_simplified):
    """
    Fetches full album/single details including ALL tracks.
    Audio features are SKIPPED due to API limitations (as of late 2024).

    Args:
        sp_client: Authenticated spotipy client instance for initial batch fetch.
        releases_simplified (list): List of simplified release objects from artist_albums.

    Returns:
        list: List of full release detail objects, including paginated tracks.
    """
    # Function body remains the same...
    if not sp_client:
        print("[Release Details] Error: Invalid Spotify client provided.")
        return []
    if not releases_simplified:
        print("[Release Details] No simplified releases provided to fetch details for.")
        return []

    full_releases_details = []
    release_ids = [r['id'] for r in releases_simplified if r and r.get('id')]
    if not release_ids:
        print("[Release Details] No valid release IDs found in the input list.")
        return []

    print(f"[Release Details] Starting full detail fetch for {len(release_ids)} releases...")
    start_time = time.time()

    # 1. Fetch full album objects in batches
    fetched_albums_map = {}
    batch_size = 20 # Max allowed by sp.albums endpoint
    for i in range(0, len(release_ids), batch_size):
        batch_ids = release_ids[i:i + batch_size]
        try:
            print(f"  Fetching album batch {i//batch_size + 1} (IDs: {batch_ids})...")
            # Use the passed client 'sp_client'
            results = sp_client.albums(batch_ids)
            if results and results.get('albums'):
                count = 0
                for album in results['albums']:
                    if album:
                        fetched_albums_map[album['id']] = album
                        count += 1
                print(f"    Successfully fetched details for {count} albums in this batch.")
            else:
                print(f"    Warning: No album data returned for batch IDs: {batch_ids}")
                for bid in batch_ids:
                    if bid not in fetched_albums_map:
                         fetched_albums_map[bid] = {'id': bid, 'name': 'Error Fetching', 'error': True, 'tracks': {'items': []}}

        except spotipy.exceptions.SpotifyException as e:
            print(f"    Spotify API error fetching album batch: {e}")
            if e.http_status == 401 or e.http_status == 403:
                print("    -> Authorization error during batch album fetch. Check Client ID/Secret. Aborting.")
                # No session to clear, just abort
                return [] # Abort if auth fails
            # Mark failed IDs
            for bid in batch_ids:
                 if bid not in fetched_albums_map:
                     fetched_albums_map[bid] = {'id': bid, 'name': f'Error {e.http_status}', 'error': True, 'tracks': {'items': []}}
        except Exception as e:
            print(f"    Unexpected error fetching album batch: {e}")
            traceback.print_exc()
            # Mark failed IDs
            for bid in batch_ids:
                 if bid not in fetched_albums_map:
                     fetched_albums_map[bid] = {'id': bid, 'name': 'Unexpected Error', 'error': True, 'tracks': {'items': []}}


    print(f"[Release Details] Finished initial album detail fetch in {time.time() - start_time:.2f}s.")
    if not fetched_albums_map:
        print("[Release Details] No albums could be fetched.")
        return []

    # 2. For each fetched album, paginate through ALL its tracks
    print("[Release Details] Fetching all tracks for each release (Audio Features SKIPPED)...")
    overall_start_time_tracks = time.time()
    processed_releases = []

    for release_id in release_ids: # Iterate in original order
        if release_id not in fetched_albums_map:
            print(f"  Skipping release ID {release_id} as it wasn't fetched successfully.")
            continue

        release_details = fetched_albums_map[release_id]
        if release_details.get('error'):
             print(f"  Skipping release '{release_details.get('name', release_id)}' due to previous fetch error.")
             processed_releases.append(release_details) # Keep error entry
             continue

        single_release_start_time = time.time()
        release_name = release_details.get('name', 'Unknown Release')
        print(f"  Processing tracks for: '{release_name}' ({release_id})")

        if 'tracks' not in release_details or not isinstance(release_details['tracks'], dict):
            release_details['tracks'] = {'items': [], 'next': None} # Initialize if missing

        all_tracks_this_release = []
        tracks_pager = release_details['tracks']

        if tracks_pager and tracks_pager.get('items'):
            all_tracks_this_release.extend(tracks_pager['items'])

        # Get a client instance for pagination. Since client credentials tokens
        # are longer-lived and managed by the manager, using the initial sp_client
        # passed to this function is generally acceptable. If token expiry during
        # a long pagination becomes an issue, we might need to re-get the client.
        # For now, let's use the provided sp_client for pagination.
        current_sp = sp_client
        # ---- Alternative: Re-fetch client if needed ----
        # current_sp = get_spotify_client_credentials_client()
        # if not current_sp:
        #      print(f"    FATAL: Cannot get Spotify client for track pagination on '{release_name}'. Aborting.")
        #      release_details['tracks_error'] = "Client unavailable for pagination"
        #      processed_releases.append(release_details)
        #      return processed_releases
        # ---- End Alternative ----

        page_num = 1; retries = 0; max_retries = 2
        while tracks_pager and tracks_pager.get('next') and retries <= max_retries:
            page_num += 1
            print(f"    Fetching track page {page_num} for '{release_name}'...")
            try:
                time.sleep(0.1)
                # Use the client obtained for pagination
                tracks_pager = current_sp.next(tracks_pager)
                if tracks_pager and tracks_pager.get('items'):
                   all_tracks_this_release.extend(tracks_pager['items'])
                   retries = 0 # Reset retries on success
                else:
                    print(f"    No more items found on page {page_num} or pager became invalid.")
                    tracks_pager = None # Ensure loop terminates
            except spotipy.exceptions.SpotifyException as e:
                print(f"    Spotify API error fetching next track page ({page_num}): {e}")
                if e.http_status == 401 or e.http_status == 403:
                    print("    --> Authorization error during track pagination. Check Client ID/Secret. Aborting.")
                    release_details['tracks_error'] = "Authorization error during pagination"
                    processed_releases.append(release_details)
                    return processed_releases # Abort
                elif e.http_status == 429: # Rate limit
                     wait_time = int(e.headers.get('Retry-After', 2)) + 1
                     print(f"    Rate limited. Waiting {wait_time} seconds...")
                     time.sleep(wait_time)
                     retries += 1; print(f"    Retrying page {page_num} (Attempt {retries}/{max_retries})")
                else:
                     release_details['tracks_error'] = f"API Error page {page_num}: {e.msg}"
                     tracks_pager = None; break
            except Exception as e:
                print(f"    Unexpected error fetching next track page ({page_num}): {e}")
                traceback.print_exc()
                release_details['tracks_error'] = f"Unexpected error page {page_num}"
                tracks_pager = None; break

        release_details['tracks']['items'] = all_tracks_this_release
        release_details['tracks'].pop('next', None)
        release_details['total_tracks_fetched'] = len(all_tracks_this_release)
        print(f"    Collected {len(all_tracks_this_release)} total tracks for '{release_name}'.")

        processed_releases.append(release_details)
        print(f"  Finished processing '{release_name}' in {time.time() - single_release_start_time:.2f}s")

    print(f"[Release Details] Finished fetching all tracks (Audio Features SKIPPED) in {time.time() - overall_start_time_tracks:.2f}s.")
    return processed_releases

# --- END OF FILE app/spotify/data.py ---