import os
import time
import json
import traceback
from flask import (
    render_template, redirect, url_for, session, flash, request, Response,
    stream_with_context, jsonify, current_app
)

from . import playlists_bp
from ..spotify.auth import get_spotify_client
from ..spotify.data import fetch_similar_genre_artists_data # Needed for keyword generation
from ..spotify.utils import parse_follower_count # Needed for sorting playlists
from .playlistsupply import login_to_playlistsupply, scrape_playlistsupply # Import scraper
from .email import generate_email_content, send_single_email, format_error_message # Import email helpers


@playlists_bp.route('/playlist-finder', methods=['GET'])
def playlist_finder():
    sp = get_spotify_client()
    if not sp:
        flash('Spotify session invalid or expired. Please login.', 'error')
        return redirect(url_for('main.login')) # Redirect to main blueprint's login

    # Get artist context from session
    artist_id = session.get('main_artist_id')
    artist_name = session.get('main_artist_name')
    artist_genres = session.get('main_artist_genres', [])

    if not artist_id or not artist_name:
        flash('Please view an artist page ("My Artist" or Search) first to set the context for playlist finding.', 'warning')
        return redirect(url_for('main.artist_dashboard')) # Redirect to main dashboard

    # Get parameters from request query string
    selected_track_id = request.args.get('selected_track_id')
    song_description_from_req = request.args.get('song_description', '') # Get description for stickiness
    user_keywords_raw = request.args.get('user_keywords', '')
    search_performed = bool(selected_track_id) # Trigger search only if track is selected

    # --- Generator Function for Streaming HTTP Response ---
    def generate_response():
        top_tracks = []
        selected_track_name = None
        market = 'US' # Default market

        # --- Part 1: Render Initial Page Structure (always runs) ---
        try:
            # Fetch top tracks for the selection form
            print(f"[PlaylistFinder Stream] Fetching top tracks for {artist_name} ({artist_id}) for selection form...")
            try:
                user_info = sp.current_user()
                market = user_info.get('country', 'US')
            except Exception as user_err:
                print(f"[PlaylistFinder Stream] Warning: Could not get user's country, defaulting to US. Error: {user_err}")

            top_tracks_result = sp.artist_top_tracks(artist_id, country=market)
            if top_tracks_result and top_tracks_result.get('tracks'):
                top_tracks = top_tracks_result['tracks']
                print(f"[PlaylistFinder Stream]   Found {len(top_tracks)} top tracks.")
            else:
                print("[PlaylistFinder Stream]   No top tracks found for artist.")
                # Template handles empty list gracefully
        except spotipy.exceptions.SpotifyException as e:
            print(f"[PlaylistFinder Stream] Spotify Error fetching top tracks: {e}")
            # Log error, but proceed to render form without tracks if necessary
        except Exception as e:
            print(f"[PlaylistFinder Stream] Error fetching top tracks: {e}")
            traceback.print_exc()

        # Yield the initial HTML structure (base template + form)
        yield render_template('playlist_finder_base.html',
                              artist_name=artist_name,
                              top_tracks=top_tracks,
                              selected_track_id=selected_track_id,
                              song_description=song_description_from_req, # Pass for form stickiness
                              user_keywords=user_keywords_raw,           # Pass for form stickiness
                              search_performed=search_performed,
                              loading=search_performed, # Indicate loading state if search is active
                              playlists=None, # No results yet
                              global_error=None)

        # --- Part 2: Perform Search and Stream Results (only if track selected) ---
        if not search_performed:
            print("[PlaylistFinder Stream] No track selected in request, stopping stream.")
            return # Stop the generator if no search is needed

        # --- Search Logic ---
        final_playlists = {}
        keywords_list = []
        ps_session = None
        has_scrape_error = False
        global_error_message = None

        try:
            # Step 2.1: Get Selected Track Details
            print(f"[PlaylistFinder Stream] Search requested for track ID: {selected_track_id}")
            if not selected_track_id: raise ValueError("Selected track ID is missing.")

            selected_track = sp.track(selected_track_id, market=market)
            if not selected_track: raise ValueError(f"Track ID '{selected_track_id}' not found or invalid in market '{market}'.")

            selected_track_name = selected_track['name']
            track_artist_name = selected_track['artists'][0]['name'] if selected_track['artists'] else artist_name
            print(f"[PlaylistFinder Stream] Selected track: '{selected_track_name}' by {track_artist_name}")

            # Update browser title via JavaScript
            js_safe_track_name = json.dumps(selected_track_name) # Safely encode for JS
            yield f'<script>document.title = "Searching playlists for " + {js_safe_track_name} + "...";</script>\n'

            # Step 2.2: Fetch Similar Artists (for keywords)
            print("[PlaylistFinder Stream] Fetching similar genre artists for keywords...")
            # Use the main artist's genres stored in session
            similar_artists = fetch_similar_genre_artists_data(sp, artist_id, artist_name, artist_genres, limit=5)

            # Step 2.3: Generate Keywords for PlaylistSupply
            keywords = set()
            keywords.add(track_artist_name) # Artist of the track
            if artist_name != track_artist_name: keywords.add(artist_name) # Artist being viewed (if different)
            keywords.add(f"{selected_track_name} {track_artist_name}") # Track + Artist
            for genre in artist_genres[:3]: keywords.add(genre.lower()) # Top 3 genres
            for sim_artist in similar_artists: keywords.add(sim_artist["name"]) # Similar artist names
            # Add user-provided keywords
            user_kws = [kw.strip().lower() for kw in user_keywords_raw.split(',') if kw.strip()]
            for kw in user_kws: keywords.add(kw)
            # Add song description words? Maybe too noisy. Stick to tags/names.

            keywords_list = list(filter(None, keywords)) # Remove empty strings
            if not keywords_list: raise ValueError("No valid keywords could be generated for the search.")
            print(f"[PlaylistFinder Stream] Generated {len(keywords_list)} keywords for PlaylistSupply: {keywords_list}")
            total_keywords = len(keywords_list)

            # Step 2.4: Get PlaylistSupply Credentials and Login
            ps_user = current_app.config.get('PLAYLIST_SUPPLY_USER')
            ps_pass = current_app.config.get('PLAYLIST_SUPPLY_PASS')
            if not ps_user or not ps_pass:
                raise ValueError("PlaylistSupply credentials (PLAYLIST_SUPPLY_USER, PLAYLIST_SUPPLY_PASS) are not configured in the environment.")

            print("[PlaylistFinder Stream] Attempting PlaylistSupply login...")
            yield f'<script>updateProgress(5, "Logging in...");</script>\n' # Show login progress
            ps_session = login_to_playlistsupply(ps_user, ps_pass)
            if not ps_session:
                raise ConnectionError("Failed to log in to PlaylistSupply. Check credentials or service status.")
            print("[PlaylistFinder Stream] PlaylistSupply login successful.")

            # Step 2.5: Scrape PlaylistSupply for each keyword (Streaming Progress)
            print("[PlaylistFinder Stream] Starting PlaylistSupply scraping...")
            processed_keywords = 0
            initial_progress = 10 # Start progress after login
            scrape_progress_range = 80 # Allocate 80% of progress bar to scraping

            for keyword in keywords_list:
                processed_keywords += 1
                # Calculate progress percentage within the allocated range
                progress = initial_progress + int((processed_keywords / total_keywords) * scrape_progress_range)
                js_keyword = json.dumps(keyword) # Safe JS encoding
                yield f'<script>updateProgress({progress}, "Searching: " + {js_keyword});</script>\n'
                print(f"  Scraping for keyword: '{keyword}' ({processed_keywords}/{total_keywords})")

                scrape_result = scrape_playlistsupply(keyword, ps_user, ps_session)
                time.sleep(0.4) # Be nice to the external service

                if scrape_result is None:
                    print(f"    Critical error (None result) during scrape for '{keyword}'.")
                    has_scrape_error = True # Mark that an error occurred
                    continue # Try next keyword
                elif isinstance(scrape_result, dict) and "error" in scrape_result:
                    err_msg = scrape_result.get('message', 'Unknown scrape error')
                    print(f"    Specific error for '{keyword}': {err_msg}")
                    has_scrape_error = True
                    if scrape_result.get("error") == "session_invalid":
                        print("    PlaylistSupply session became invalid. Stopping scrape.")
                        global_error_message = "PlaylistSupply session expired or became invalid during search."
                        break # Stop scraping if session is dead
                    continue # Try next keyword otherwise
                elif isinstance(scrape_result, list):
                    found_count = 0
                    for pl in scrape_result:
                        # Basic validation of playlist dictionary structure
                        if isinstance(pl, dict) and pl.get('url') and 'open.spotify.com/playlist/' in pl['url']:
                            # Use URL as key to ensure uniqueness
                            if pl['url'] not in final_playlists:
                                final_playlists[pl['url']] = pl
                                found_count += 1
                        # else: print(f"    Skipping invalid playlist entry for '{keyword}': {pl}")
                    if found_count > 0: print(f"    Added {found_count} new unique playlists for '{keyword}'.")
                else:
                    print(f"    Warning: Unexpected result type ({type(scrape_result)}) for '{keyword}'.")
                    has_scrape_error = True # Mark error but continue

            # Step 2.6: Sort Results by Followers (Descending)
            print(f"[PlaylistFinder Stream] Aggregated {len(final_playlists)} unique playlists.")
            if final_playlists:
                yield f'<script>updateProgress(95, "Sorting results...");</script>\n'
                print("[PlaylistFinder Stream] Sorting playlists by follower count...")
                # Use the helper function for parsing follower counts robustly
                sorted_playlists = sorted(
                    list(final_playlists.values()),
                    key=lambda p: parse_follower_count(p.get('followers')) or 0, # Default to 0 if parse fails
                    reverse=True
                )
                print("[PlaylistFinder Stream] Sorting complete.")
            else:
                sorted_playlists = []
                print("[PlaylistFinder Stream] No playlists found after scraping.")

            # Step 2.7: Render Final Results HTML to a String
            results_html = render_template('playlist_finder_results.html',
                                           selected_track_name=selected_track_name,
                                           playlists=sorted_playlists,
                                           has_scrape_error=has_scrape_error, # Indicate if any scrape failed
                                           search_performed=True,
                                           global_error=global_error_message) # Pass global error if session died

            # Step 2.8: Escape HTML and Playlist Data, Yield JS to Inject Results
            js_escaped_html = json.dumps(results_html)
            # Serialize playlist data (ensure it's JSON serializable)
            serializable_playlists = []
            for pl in sorted_playlists:
                 # Clean up data if necessary, e.g., ensure types are standard JSON types
                 # For now, assume the scraped data is mostly fine
                 serializable_playlists.append(pl)
            js_escaped_playlist_data = json.dumps(serializable_playlists)

            js_safe_final_title = json.dumps(f"Playlist Results for {selected_track_name}")
            yield f'<script>injectResultsAndData({js_escaped_html}, {js_escaped_playlist_data}); document.title = {js_safe_final_title}; hideProgress();</script>\n'

        except (ValueError, ConnectionError, spotipy.exceptions.SpotifyException) as e:
            # Handle known exceptions gracefully
            error_occurred = str(e)
            print(f"[PlaylistFinder Stream] Error during playlist search: {error_occurred}")
            js_safe_error = json.dumps(error_occurred) # Use json.dumps for safety
            yield f'<script>showSearchError("Playlist Search Error: " + {js_safe_error});</script>\n'
            # Also render the error in the results partial if possible
            error_html = render_template('playlist_finder_results.html',
                                          selected_track_name=selected_track_name, # May be None if track fetch failed
                                          playlists=None,
                                          search_performed=True,
                                          global_error=error_occurred)
            js_escaped_error_html = json.dumps(error_html)
            yield f'<script>injectResultsAndData({js_escaped_error_html}, []); hideProgress();</script>\n'


        except Exception as e:
            # Handle unexpected errors
            error_occurred = f"An unexpected error occurred: {str(e)}"
            print(f"[PlaylistFinder Stream] Unexpected Error: {e}")
            traceback.print_exc()
            js_safe_error = json.dumps(error_occurred)
            yield f'<script>showSearchError("Unexpected Error: " + {js_safe_error});</script>\n'
            # Render error in results partial
            error_html = render_template('playlist_finder_results.html',
                                          selected_track_name=selected_track_name,
                                          playlists=None,
                                          search_performed=True,
                                          global_error=error_occurred)
            js_escaped_error_html = json.dumps(error_html)
            yield f'<script>injectResultsAndData({js_escaped_error_html}, []); hideProgress();</script>\n'

        finally:
            # Ensure progress bar is hidden regardless of outcome
             yield f'<script>hideProgress();</script>\n'


    # Return the streaming response
    return Response(stream_with_context(generate_response()), mimetype='text/html')


# --- Email Related Routes ---

@playlists_bp.route('/generate-preview-email', methods=['POST'])
def generate_preview_email_route():
    sp = get_spotify_client()
    if not sp:
        return jsonify({"error": "Spotify authentication required."}), 401
    if not current_app.config.get('GEMINI_API_KEY'):
        return jsonify({"error": "AI API Key is not configured."}), 500

    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing request data."}), 400

    track_id = data.get('track_id')
    song_description = data.get('song_description')
    playlist = data.get('playlist') # Expecting a single playlist object

    if not track_id or not song_description or not playlist or not isinstance(playlist, dict):
        return jsonify({"error": "Missing required fields: track_id, song_description, playlist object."}), 400

    try:
        # Fetch track details using Spotify client
        market = 'US' # Default market
        try: market = sp.current_user().get('country', 'US')
        except Exception: pass # Ignore error getting market

        track = sp.track(track_id, market=market)
        if not track:
            raise ValueError(f"Could not fetch details for track ID: {track_id}")

        # Call the email generation helper function
        subject, body = generate_email_content(track, playlist, song_description)

        return jsonify({"subject": subject, "body": body})

    except spotipy.exceptions.SpotifyException as e:
         print(f"Spotify Error generating preview: {e}")
         return jsonify({"error": f"Spotify API error: {e.msg}"}), 502 # Bad Gateway or relevant code
    except ValueError as e:
         print(f"Value Error generating preview: {e}")
         return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"Error generating email preview: {e}")
        traceback.print_exc()
        # Use the helper to format the error message
        error_msg = format_error_message(e, default="Failed to generate email preview due to an unexpected error.")
        return jsonify({"error": error_msg}), 500


@playlists_bp.route('/send-emails', methods=['POST'])
def send_emails_route():
    sp = get_spotify_client()
    if not sp:
        # Cannot use stream_with_context for error before generator starts easily
        return Response("event: error\ndata: Spotify authentication required\n\n", mimetype='text/event-stream', status=401)

    if not current_app.config.get('GEMINI_API_KEY'):
         return Response("event: error\ndata: AI API Key not configured\n\n", mimetype='text/event-stream', status=500)

    # Check SMTP configuration
    sender_email = current_app.config.get("SENDER_EMAIL")
    sender_password = current_app.config.get("SENDER_PASSWORD")
    smtp_server = current_app.config.get("SMTP_SERVER")
    smtp_port = current_app.config.get("SMTP_PORT")
    if not all([sender_email, sender_password, smtp_server, smtp_port]):
        return Response("event: error\ndata: Email sender (SMTP) credentials or server info missing\n\n", mimetype='text/event-stream', status=500)

    data = request.get_json()
    if not data:
        return Response("event: error\ndata: Missing request data\n\n", mimetype='text/event-stream', status=400)

    selected_track_id = data.get('track_id')
    song_description = data.get('song_description')
    playlists_to_contact = data.get('playlists', []) # Expecting a list of playlist objects

    if not selected_track_id or not song_description or not isinstance(playlists_to_contact, list):
        return Response("event: error\ndata: Missing required fields: track_id, song_description, playlists list\n\n", mimetype='text/event-stream', status=400)

    if not playlists_to_contact:
         return Response("event: status\ndata: No playlists provided to contact.\nevent: done\ndata: Finished.\n\n", mimetype='text/event-stream')


    # --- Generator for Streaming Email Sending Progress ---
    def email_stream():
        track = None
        total_emails_to_send = len(playlists_to_contact)
        sent_count = 0
        error_count = 0
        start_time = time.time()

        # Function to safely yield messages
        def yield_message(event, data):
            # Basic sanitization: replace newlines in data to prevent corrupting SSE format
            sanitized_data = str(data).replace('\n', ' ').replace('\r', '')
            yield f"event: {event}\ndata: {sanitized_data}\n\n"

        try:
            # Fetch track details once at the beginning
            yield_message('status', 'Fetching track details...')
            market = 'US'
            try: market = sp.current_user().get('country', 'US')
            except Exception: pass
            track = sp.track(selected_track_id, market=market)
            if not track:
                raise ValueError(f"Cannot fetch track details for ID: {selected_track_id}")
            yield_message('status', f"Track '{track.get('name', 'N/A')}' details fetched.")

        except (ValueError, spotipy.exceptions.SpotifyException) as e:
            error_msg = format_error_message(e, "Error fetching initial track details")
            yield_message('error', error_msg)
            yield_message('done', 'Aborted due to track fetch error.')
            return # Stop processing if track details fail

        # --- Loop through playlists and send emails ---
        for i, playlist in enumerate(playlists_to_contact):
            if not isinstance(playlist, dict):
                yield_message('status', f"Skipping invalid playlist entry at index {i}.")
                continue

            playlist_name = playlist.get('name', 'N/A')
            curator_email = playlist.get('email')

            current_status = f"({i+1}/{total_emails_to_send}) Processing playlist '{playlist_name}'"
            yield_message('status', current_status)

            if not curator_email or '@' not in curator_email:
                yield_message('status', f"-> Skipping '{playlist_name}' (no valid email found).")
                continue

            try:
                # 1. Generate Email Content using helper
                yield_message('status', f"-> Generating AI content for {curator_email}...")
                subject, body = generate_email_content(track, playlist, song_description)
                if not body: # Should not happen if helper raises error, but check anyway
                    raise ValueError("AI failed to generate email body.")
                yield_message('status', f"-> AI content generated.")

                # 2. Send Email using helper
                yield_message('status', f"-> Sending email to {curator_email}...")
                send_single_email(
                    recipient=curator_email,
                    subject=subject,
                    body=body,
                    sender_email=sender_email,
                    sender_password=sender_password,
                    smtp_server=smtp_server,
                    smtp_port=smtp_port
                )
                yield_message('success', f"-> Email sent successfully to {curator_email} for '{playlist_name}'.")
                sent_count += 1

                # 3. Wait before sending next email (rate limiting)
                # Make delay configurable?
                sleep_duration = 6 # Increased delay slightly
                if i < total_emails_to_send - 1: # Don't wait after the last email
                    yield_message('status', f"-> Waiting {sleep_duration}s before next email...")
                    time.sleep(sleep_duration)

            except Exception as e:
                error_count += 1
                error_msg = format_error_message(e, f"Failed processing email for {curator_email}")
                print(f"Error sending email to {curator_email}: {e}")
                # traceback.print_exc() # Optional: log full traceback server-side
                yield_message('error', f"-> Error for {curator_email}: {error_msg}")
                # Decide whether to continue or stop on error? Continue for now.
                # Add a small delay even after error to avoid hammering
                time.sleep(1)


        # --- Final Summary ---
        end_time = time.time()
        duration = round(end_time - start_time)
        final_message = f"Email process finished in {duration}s. Sent: {sent_count}, Errors: {error_count} / {total_emails_to_send} attempted."
        yield_message('done', final_message)

    # Return the streaming response
    return Response(email_stream(), mimetype='text/event-stream')