# --- START OF FILE app/spotify/data.py ---
import time
import traceback
from flask import session # Keep for potential error handling
import spotipy
from .auth import get_spotify_client_credentials_client

# --- Helper Function for Genre-Based Similarity (REFINED) ---
# --- START OF FILE app/spotify/data.py ---
import time
import traceback
from flask import session
import spotipy
from .auth import get_spotify_client_credentials_client

# --- Helper Function for Genre-Based Similarity (REFINED) ---
# INCREASED candidates_per_genre default
def fetch_similar_artists_by_genre(sp_client, artist_id, artist_name, artist_genres, candidates_per_genre=100000): # Fetch max allowed
    """
    Fetches artists based on genre similarity using Spotify search. Fetches a larger pool.

    Args:
        sp_client: Authenticated spotipy client instance.
        artist_id (str): Spotify ID of the source artist (to exclude).
        artist_name (str): Name of the source artist (for logging).
        artist_genres (list): List of genres for the source artist.
        candidates_per_genre (int): How many artists to fetch per genre search query (max 50).

    Returns:
        list: A list of full artist objects considered similar based on genre. De-duplicated.
    """
    if not sp_client: print("[Similar By Genre] Error: Invalid Spotify client."); return []
    if not artist_id or not artist_genres: print(f"[Similar By Genre] Missing ID or genres for {artist_name}."); return []

    genres_to_search = artist_genres[:3] # Use top 3 genres
    if not genres_to_search: print(f"[Similar By Genre] No genres for {artist_name}."); return []

    print(f"[Similar By Genre] Searching based on genres: {', '.join(genres_to_search)}")
    similar_artists_map = {}
    # Ensure limit doesn't exceed Spotify's max of 50
    actual_limit_per_genre = min(candidates_per_genre, 50)

    try:
        for genre in genres_to_search:
            query = f'genre:"{genre}"'
            print(f"[Similar By Genre]   Searching: {query} (limit {actual_limit_per_genre})")
            try:
                # Fetch up to the max allowed per genre
                results = sp_client.search(q=query, type='artist', limit=actual_limit_per_genre)
                if results and results.get('artists') and results['artists'].get('items'):
                    count = 0
                    for artist in results['artists']['items']:
                        if artist and artist['id'] != artist_id and artist['id'] not in similar_artists_map:
                            similar_artists_map[artist['id']] = artist
                            count += 1
                    print(f"[Similar By Genre]   Found {count} new unique artists for '{genre}'.")
                else:
                    print(f"[Similar By Genre]   No results for '{genre}'.")
                time.sleep(0.15)
            except spotipy.exceptions.SpotifyException as search_err:
                print(f"[Similar By Genre]   Spotify Error searching '{genre}': {search_err}")
            except Exception as search_ex:
                print(f"[Similar By Genre]   Unexpected Error searching '{genre}': {search_ex}")
                traceback.print_exc()

        final_list = list(similar_artists_map.values())
        print(f"[Similar By Genre] Fetched {len(final_list)} unique candidate artists pool.")
        return final_list

    except Exception as e:
        print(f"[Similar By Genre] Unexpected error during overall process: {e}")
        traceback.print_exc()
        return []
    

# --- Keep fetch_release_details ---
def fetch_release_details(sp_client, releases_simplified):
    # ... Function body remains the same ...
    if not sp_client: print("[Release Details] Error: Invalid Spotify client."); return []
    if not releases_simplified: print("[Release Details] No simplified releases provided."); return []
    release_ids = [r['id'] for r in releases_simplified if r and r.get('id')]
    if not release_ids: print("[Release Details] No valid release IDs found."); return []
    print(f"[Release Details] Starting full detail fetch for {len(release_ids)} releases...")
    start_time = time.time()
    fetched_albums_map = {}
    batch_size = 20
    for i in range(0, len(release_ids), batch_size):
        batch_ids = release_ids[i:i + batch_size]
        try:
            print(f"  Fetching album batch {i//batch_size + 1}...")
            results = sp_client.albums(batch_ids)
            if results and results.get('albums'):
                count = 0
                for album in results['albums']:
                    if album: fetched_albums_map[album['id']] = album; count += 1
                print(f"    Fetched details for {count} albums.")
            else:
                print(f"    Warning: No album data for batch IDs: {batch_ids}")
                for bid in batch_ids: fetched_albums_map.setdefault(bid, {'id': bid, 'name': 'Error Fetching', 'error': True, 'tracks': {'items': []}})
        except spotipy.exceptions.SpotifyException as e:
            print(f"    Spotify API error fetching album batch: {e}")
            if e.http_status == 401 or e.http_status == 403: print("    -> Auth error. Aborting."); return []
            for bid in batch_ids: fetched_albums_map.setdefault(bid, {'id': bid, 'name': f'Error {e.http_status}', 'error': True, 'tracks': {'items': []}})
        except Exception as e:
            print(f"    Unexpected error fetching album batch: {e}"); traceback.print_exc()
            for bid in batch_ids: fetched_albums_map.setdefault(bid, {'id': bid, 'name': 'Unexpected Error', 'error': True, 'tracks': {'items': []}})
    print(f"[Release Details] Finished initial album details in {time.time() - start_time:.2f}s.")
    if not fetched_albums_map: print("[Release Details] No albums could be fetched."); return []
    print("[Release Details] Fetching all tracks (Audio Features SKIPPED)...")
    overall_start_time_tracks = time.time()
    processed_releases = []
    for release_id in release_ids:
        if release_id not in fetched_albums_map: continue
        release_details = fetched_albums_map[release_id]
        if release_details.get('error'): processed_releases.append(release_details); continue
        single_release_start_time = time.time(); release_name = release_details.get('name', 'Unknown')
        print(f"  Processing tracks for: '{release_name}' ({release_id})")
        if 'tracks' not in release_details or not isinstance(release_details['tracks'], dict): release_details['tracks'] = {'items': [], 'next': None}
        all_tracks_this_release = []
        tracks_pager = release_details['tracks']
        if tracks_pager and tracks_pager.get('items'): all_tracks_this_release.extend(tracks_pager['items'])
        current_sp = sp_client # Use initial client for pagination for now
        page_num = 1; retries = 0; max_retries = 2
        while tracks_pager and tracks_pager.get('next') and retries <= max_retries:
            page_num += 1; print(f"    Fetching track page {page_num} for '{release_name}'...")
            try:
                time.sleep(0.1); tracks_pager = current_sp.next(tracks_pager)
                if tracks_pager and tracks_pager.get('items'): all_tracks_this_release.extend(tracks_pager['items']); retries = 0
                else: tracks_pager = None
            except spotipy.exceptions.SpotifyException as e:
                print(f"    Spotify API error fetching next track page ({page_num}): {e}")
                if e.http_status == 401 or e.http_status == 403: print("    --> Auth error. Aborting."); release_details['tracks_error'] = "Auth error"; processed_releases.append(release_details); return processed_releases
                elif e.http_status == 429: wait_time = int(e.headers.get('Retry-After', 2)) + 1; print(f"    Rate limited. Waiting {wait_time}s..."); time.sleep(wait_time); retries += 1; print(f"    Retrying page {page_num} ({retries}/{max_retries})")
                else: release_details['tracks_error'] = f"API Error page {page_num}: {e.msg}"; tracks_pager = None; break
            except Exception as e: print(f"    Unexpected error fetching next track page ({page_num}): {e}"); traceback.print_exc(); release_details['tracks_error'] = f"Unexpected error page {page_num}"; tracks_pager = None; break
        release_details['tracks']['items'] = all_tracks_this_release; release_details['tracks'].pop('next', None)
        release_details['total_tracks_fetched'] = len(all_tracks_this_release); print(f"    Collected {len(all_tracks_this_release)} total tracks for '{release_name}'.")
        processed_releases.append(release_details)
        print(f"  Finished processing '{release_name}' in {time.time() - single_release_start_time:.2f}s")
    print(f"[Release Details] Finished fetching all tracks (Audio Features SKIPPED) in {time.time() - overall_start_time_tracks:.2f}s.")
    return processed_releases


def fetch_spotify_details_for_names(sp_client, artist_names):
    """
    Searches Spotify for artists by name and fetches full details for matches.

    Args:
        sp_client: Authenticated spotipy client instance.
        artist_names (list): A list of artist names (strings).

    Returns:
        list: A list of full Spotify artist objects for found artists. De-duplicated.
    """
    if not sp_client:
        print("[Spotify Lookup] Error: Invalid Spotify client.")
        return []
    if not artist_names:
        print("[Spotify Lookup] No artist names provided.")
        return []

    print(f"[Spotify Lookup] Searching Spotify for {len(artist_names)} names...")
    found_artists_map = {} # Use map to store by ID for deduplication

    processed_count = 0
    for name in artist_names:
        processed_count += 1
        if not name or not isinstance(name, str):
            continue

        # Add slight delay to avoid hitting search rate limits too quickly
        if processed_count > 1:
             time.sleep(0.1) # Small delay between searches

        try:
            # print(f"  Searching Spotify for: '{name}' ({processed_count}/{len(artist_names)})")
            # Search for the artist, limit 1 is usually sufficient
            results = sp_client.search(q=name, type='artist', limit=1)

            if results and results['artists']['items']:
                found_artist_basic = results['artists']['items'][0]
                artist_id = found_artist_basic.get('id')

                # Check if we already fetched this ID
                if artist_id and artist_id not in found_artists_map:
                    # Fetch full details (search results might be incomplete)
                    # print(f"    Found potential match: {found_artist_basic['name']} ({artist_id}). Fetching full details.")
                    try:
                        # Fetching full details ensures we have popularity, followers etc.
                        full_details = sp_client.artist(artist_id)
                        if full_details:
                            found_artists_map[artist_id] = full_details
                        # else: print(f"    Warning: Could not fetch full details for ID {artist_id}")
                    except spotipy.exceptions.SpotifyException as detail_err:
                         print(f"    Spotify Error fetching full details for {artist_id} ('{name}'): {detail_err}")
                    except Exception as detail_ex:
                         print(f"    Unexpected Error fetching full details for {artist_id} ('{name}'): {detail_ex}")

            # else: print(f"  No direct match found on Spotify for '{name}'")

        except spotipy.exceptions.SpotifyException as search_err:
             # Handle specific errors like rate limiting if needed
             if search_err.http_status == 429:
                 print(f"  Spotify rate limit hit while searching for '{name}'. Stopping lookup.")
                 break # Stop if rate limited
             else:
                 print(f"  Spotify Error searching for '{name}': {search_err}")
        except Exception as search_ex:
            print(f"  Unexpected Error searching for '{name}': {search_ex}")
            traceback.print_exc()

    final_list = list(found_artists_map.values())
    print(f"[Spotify Lookup] Found Spotify details for {len(final_list)} unique artists.")
    return final_list

# --- END OF FILE app/spotify/data.py ---