# --- START OF FILE app/playlists/routes.py ---
import os
import time
import json
import traceback
from flask import (
    render_template, redirect, url_for, flash, request, Response,
    stream_with_context, jsonify, current_app
)
import spotipy

from . import playlists_bp
# Import the NEW client credentials function
from ..spotify.auth import get_spotify_client_credentials_client
from ..spotify.data import fetch_similar_genre_artists_data # Needed for keyword generation
from ..spotify.utils import parse_follower_count # Needed for sorting playlists
from .playlistsupply import login_to_playlistsupply, scrape_playlistsupply # Import scraper
from .email import generate_email_content, send_single_email, format_error_message # Import email helpers


@playlists_bp.route('/playlist-finder/<artist_id>', methods=['GET'])
def playlist_finder(artist_id):
    # Use Client Credentials client - no user login needed for Spotify public data
    sp = get_spotify_client_credentials_client()
    if not sp:
        flash('Spotify API client could not be initialized. Check configuration.', 'error')
        # Redirect back to search or show error? Redirecting to search.
        return redirect(url_for('main.search_artist'))

    if not artist_id:
        flash('No artist ID provided for playlist finding.', 'error')
        return redirect(url_for('main.search_artist'))

    # Fetch artist details to get name and genres for context/keywords
    try:
        print(f"[PlaylistFinder] Fetching details for artist ID: {artist_id}")
        artist = sp.artist(artist_id)
        if not artist:
            flash(f"Could not find details for artist ID {artist_id}.", 'error')
            return redirect(url_for('main.search_artist'))
        artist_name = artist.get('name', 'Selected Artist')
        artist_genres = artist.get('genres', [])
        print(f"[PlaylistFinder] Context artist: {artist_name}")
    except spotipy.exceptions.SpotifyException as e:
        print(f"Spotify Error fetching artist details for playlist finder: {e}")
        flash(f"Could not fetch artist details: {e.msg}", 'error')
        return redirect(url_for('main.search_artist'))
    except Exception as e:
        print(f"Unexpected Error fetching artist details for playlist finder: {e}")
        flash("An unexpected error occurred fetching artist details.", 'error')
        traceback.print_exc()
        return redirect(url_for('main.search_artist'))

    # Get parameters from request query string
    selected_track_id = request.args.get('selected_track_id')
    song_description_from_req = request.args.get('song_description', '') # For stickiness
    user_keywords_raw = request.args.get('user_keywords', '')
    # Determine if a search should be performed based on track selection
    search_performed = bool(selected_track_id)

    # --- Generator Function for Streaming HTTP Response ---
    def generate_response():
        top_tracks = []
        selected_track_name = None
        market = 'US' # Default market for top tracks when no user context

        # --- Part 1: Render Initial Page Structure (always runs) ---
        try:
            # Fetch top tracks for the selection form *using the provided artist_id*
            print(f"[PlaylistFinder Stream] Fetching top tracks for {artist_name} ({artist_id})...")
            top_tracks_result = sp.artist_top_tracks(artist_id, country=market)
            if top_tracks_result and top_tracks_result.get('tracks'):
                top_tracks = top_tracks_result['tracks']
                print(f"[PlaylistFinder Stream]   Found {len(top_tracks)} top tracks in market {market}.")
            else:
                print(f"[PlaylistFinder Stream]   No top tracks found for artist {artist_id} in market {market}.")
                # Template handles empty list
        except spotipy.exceptions.SpotifyException as e:
            print(f"[PlaylistFinder Stream] Spotify Error fetching top tracks: {e}")
            # Log error, but proceed to render form without tracks if necessary
        except Exception as e:
            print(f"[PlaylistFinder Stream] Error fetching top tracks: {e}")
            traceback.print_exc()

        # Yield the initial HTML structure (base template + form)
        yield render_template('playlist_finder_base.html',
                              # Pass artist info fetched above
                              artist_id=artist_id,
                              artist_name=artist_name,
                              artist_genres=artist_genres, # Pass genres too
                              top_tracks=top_tracks,
                              selected_track_id=selected_track_id,
                              song_description=song_description_from_req,
                              user_keywords=user_keywords_raw,
                              search_performed=search_performed,
                              loading=search_performed,
                              playlists=None,
                              global_error=None)

        # --- Part 2: Perform Search and Stream Results (only if track selected) ---
        if not search_performed:
            print("[PlaylistFinder Stream] No track selected in request, stopping stream.")
            return # Stop the generator

        # --- Search Logic (largely same as before, uses fetched artist context) ---
        final_playlists = {}
        keywords_list = []
        ps_session = None
        has_scrape_error = False
        global_error_message = None

        try:
            # Step 2.1: Get Selected Track Details (already have artist_name, artist_genres)
            print(f"[PlaylistFinder Stream] Search requested for track ID: {selected_track_id}")
            if not selected_track_id: raise ValueError("Selected track ID is missing.")

            selected_track = sp.track(selected_track_id, market=market)
            if not selected_track: raise ValueError(f"Track ID '{selected_track_id}' not found or invalid.")
            selected_track_name = selected_track['name']
            track_artist_name = selected_track['artists'][0]['name'] if selected_track['artists'] else artist_name
            print(f"[PlaylistFinder Stream] Selected track: '{selected_track_name}' by {track_artist_name}")
            js_safe_track_name = json.dumps(selected_track_name)
            yield f'<script>document.title = "Searching playlists for " + {js_safe_track_name} + "...";</script>\n'

            # Step 2.2: Fetch Similar Artists (for keywords) - Use fetched artist context
            print("[PlaylistFinder Stream] Fetching similar genre artists for keywords...")
            similar_artists = fetch_similar_genre_artists_data(sp, artist_id, artist_name, artist_genres, limit=5)

            # Step 2.3: Generate Keywords (uses fetched artist context)
            keywords = set()
            keywords.add(track_artist_name); keywords.add(artist_name)
            keywords.add(f"{selected_track_name} {track_artist_name}")
            for genre in artist_genres[:3]: keywords.add(genre.lower())
            for sim_artist in similar_artists: keywords.add(sim_artist["name"])
            user_kws = [kw.strip().lower() for kw in user_keywords_raw.split(',') if kw.strip()]
            for kw in user_kws: keywords.add(kw)
            keywords_list = list(filter(None, keywords))
            if not keywords_list: raise ValueError("No valid keywords generated.")
            print(f"[PlaylistFinder Stream] Generated {len(keywords_list)} keywords: {keywords_list}")
            total_keywords = len(keywords_list)

            # Steps 2.4 (Login), 2.5 (Scrape), 2.6 (Sort), 2.7 (Render), 2.8 (Yield JS)
            # remain the same conceptually as in the previous version of this route
            # Ensure they handle errors and yield progress correctly.

            # Step 2.4: Credentials & Login (No change)
            ps_user = current_app.config.get('PLAYLIST_SUPPLY_USER')
            ps_pass = current_app.config.get('PLAYLIST_SUPPLY_PASS')
            if not ps_user or not ps_pass: raise ValueError("PlaylistSupply credentials missing.")
            yield f'<script>updateProgress(5, "Logging in...");</script>\n'
            ps_session = login_to_playlistsupply(ps_user, ps_pass)
            if not ps_session: raise ConnectionError("Failed to log in to PlaylistSupply.")

            # Step 2.5: Scrape (No change in logic)
            processed_keywords = 0; initial_progress = 10; scrape_progress_range = 80
            for keyword in keywords_list:
                processed_keywords += 1
                progress = initial_progress + int((processed_keywords / total_keywords) * scrape_progress_range)
                js_keyword = json.dumps(keyword); yield f'<script>updateProgress({progress}, "Searching: " + {js_keyword});</script>\n'
                print(f"  Scraping '{keyword}' ({processed_keywords}/{total_keywords})")
                scrape_result = scrape_playlistsupply(keyword, ps_user, ps_session)
                time.sleep(0.4)
                # ... (error handling and result processing for scrape_result as before) ...
                if scrape_result is None: has_scrape_error = True; continue
                elif isinstance(scrape_result, dict) and "error" in scrape_result:
                    has_scrape_error = True
                    if scrape_result.get("error") == "session_invalid": global_error_message = "PS Session Invalid"; break
                    continue
                elif isinstance(scrape_result, list):
                    for pl in scrape_result:
                        if isinstance(pl, dict) and pl.get('url') and 'open.spotify.com/playlist/' in pl['url'] and pl['url'] not in final_playlists:
                            final_playlists[pl['url']] = pl
                else: has_scrape_error = True

            # Step 2.6: Sort (No change)
            sorted_playlists = []
            if final_playlists:
                yield f'<script>updateProgress(95, "Sorting results...");</script>\n'
                sorted_playlists = sorted(list(final_playlists.values()), key=lambda p: parse_follower_count(p.get('followers')) or 0, reverse=True)
            print(f"[PlaylistFinder Stream] Aggregated {len(final_playlists)} unique playlists. Sorted {len(sorted_playlists)}.")

            # Step 2.7: Render results HTML (No change)
            results_html = render_template('playlist_finder_results.html',
                                           selected_track_name=selected_track_name,
                                           playlists=sorted_playlists,
                                           has_scrape_error=has_scrape_error,
                                           search_performed=True,
                                           global_error=global_error_message)

            # Step 2.8: Yield JS injection (No change)
            js_escaped_html = json.dumps(results_html)
            js_escaped_playlist_data = json.dumps(sorted_playlists)
            js_safe_final_title = json.dumps(f"Playlist Results for {selected_track_name}")
            yield f'<script>injectResultsAndData({js_escaped_html}, {js_escaped_playlist_data}); document.title = {js_safe_final_title}; hideProgress();</script>\n'

        # --- Error Handling (same as before) ---
        except (ValueError, ConnectionError, spotipy.exceptions.SpotifyException) as e:
            error_occurred = str(e); print(f"[PlaylistFinder Stream] Error: {error_occurred}")
            js_safe_error = json.dumps(error_occurred); yield f'<script>showSearchError("Playlist Search Error: " + {js_safe_error});</script>\n'
            error_html = render_template('playlist_finder_results.html', selected_track_name=selected_track_name, playlists=None, search_performed=True, global_error=error_occurred)
            js_escaped_error_html = json.dumps(error_html); yield f'<script>injectResultsAndData({js_escaped_error_html}, []); hideProgress();</script>\n'
        except Exception as e:
            error_occurred = f"Unexpected error: {str(e)}"; print(f"[PlaylistFinder Stream] Unexpected Error: {e}"); traceback.print_exc()
            js_safe_error = json.dumps(error_occurred); yield f'<script>showSearchError("Unexpected Error: " + {js_safe_error});</script>\n'
            error_html = render_template('playlist_finder_results.html', selected_track_name=selected_track_name, playlists=None, search_performed=True, global_error=error_occurred)
            js_escaped_error_html = json.dumps(error_html); yield f'<script>injectResultsAndData({js_escaped_error_html}, []); hideProgress();</script>\n'
        finally:
            yield f'<script>hideProgress();</script>\n'

    # Return the streaming response
    return Response(stream_with_context(generate_response()), mimetype='text/html')


# --- Email Related Routes (Modified to use Client Credentials Client) ---

@playlists_bp.route('/generate-preview-email', methods=['POST'])
def generate_preview_email_route():
    # Use Client Credentials client if Spotify interaction is needed
    sp = get_spotify_client_credentials_client()
    if not sp:
        # Cannot rely on flash here as it's an API endpoint
        return jsonify({"error": "Spotify API client failed to initialize."}), 503 # Service Unavailable

    # Rest of the logic remains the same, using the 'sp' client obtained above
    if not current_app.config.get('GEMINI_API_KEY'): return jsonify({"error": "AI API Key is not configured."}), 500
    data = request.get_json(); # ... (validation as before) ...
    if not data: return jsonify({"error": "Missing request data."}), 400
    track_id = data.get('track_id'); song_description = data.get('song_description'); playlist = data.get('playlist')
    if not track_id or not song_description or not playlist or not isinstance(playlist, dict): return jsonify({"error": "Missing required fields."}), 400

    try:
        market = 'US' # Default market
        track = sp.track(track_id, market=market)
        if not track: raise ValueError(f"Could not fetch details for track ID: {track_id}")
        subject, body = generate_email_content(track, playlist, song_description)
        return jsonify({"subject": subject, "body": body})
    except (ValueError, spotipy.exceptions.SpotifyException) as e:
         error_msg = format_error_message(e, "Preview Generation Failed")
         status_code = 400 if isinstance(e, ValueError) else 502 # Bad Gateway for upstream Spotify error
         print(f"Error generating preview: {error_msg}")
         return jsonify({"error": error_msg}), status_code
    except Exception as e:
        print(f"Unexpected error generating email preview: {e}"); traceback.print_exc()
        error_msg = format_error_message(e, default="Unexpected error generating preview.")
        return jsonify({"error": error_msg}), 500


@playlists_bp.route('/send-emails', methods=['POST'])
def send_emails_route():
    # Use Client Credentials client if Spotify interaction is needed for track details
    sp = get_spotify_client_credentials_client()
    # Check required config (Gemini, SMTP) - No change here
    if not sp: return Response("event: error\ndata: Spotify client error\n\n", mimetype='text/event-stream', status=503)
    if not current_app.config.get('GEMINI_API_KEY'): return Response("event: error\ndata: Gemini key missing\n\n", mimetype='text/event-stream', status=500)
    sender_email = current_app.config.get("SENDER_EMAIL"); # ... (check other SMTP config as before) ...
    sender_password = current_app.config.get("SENDER_PASSWORD")
    smtp_server = current_app.config.get("SMTP_SERVER")
    smtp_port = current_app.config.get("SMTP_PORT")
    if not all([sender_email, sender_password, smtp_server, smtp_port]): return Response("event: error\ndata: SMTP creds missing\n\n", mimetype='text/event-stream', status=500)

    # Get data from request - No change here
    data = request.get_json(); # ... (validation as before) ...
    if not data: return Response("event: error\ndata: Missing request data\n\n", mimetype='text/event-stream', status=400)
    selected_track_id = data.get('track_id'); song_description = data.get('song_description'); playlists_to_contact = data.get('playlists', [])
    if not selected_track_id or not song_description or not isinstance(playlists_to_contact, list): return Response("event: error\ndata: Missing required fields\n\n", mimetype='text/event-stream', status=400)
    if not playlists_to_contact: return Response("event: status\ndata: No playlists provided\nevent: done\ndata: Finished.\n\n", mimetype='text/event-stream')

    # --- Generator for Streaming (Uses the 'sp' client obtained above) ---
    def email_stream():
        track = None; total_emails_to_send = len(playlists_to_contact); sent_count = 0; error_count = 0; start_time = time.time()
        def yield_message(event, data): # Helper function (no change)
            sanitized_data = str(data).replace('\n', ' ').replace('\r', ''); yield f"event: {event}\ndata: {sanitized_data}\n\n"

        try: # Fetch track details once using the client credentials client
            yield_message('status', 'Fetching track details...')
            market = 'US'; track = sp.track(selected_track_id, market=market)
            if not track: raise ValueError(f"Cannot fetch track details for ID: {selected_track_id}")
            yield_message('status', f"Track '{track.get('name', 'N/A')}' details fetched.")
        except (ValueError, spotipy.exceptions.SpotifyException) as e:
            error_msg = format_error_message(e, "Error fetching track details"); yield_message('error', error_msg); yield_message('done', 'Aborted.'); return

        # --- Loop through playlists (Logic remains the same) ---
        for i, playlist in enumerate(playlists_to_contact):
            # ... (Validation of playlist entry) ...
            if not isinstance(playlist, dict): yield_message('status', f"Skipping invalid entry {i}."); continue
            playlist_name = playlist.get('name', 'N/A'); curator_email = playlist.get('email')
            current_status = f"({i+1}/{total_emails_to_send}) Processing '{playlist_name}'"; yield_message('status', current_status)
            if not curator_email or '@' not in curator_email: yield_message('status', f"-> Skipping (no valid email)."); continue

            try:
                # 1. Generate Content (No change needed here, uses passed track/playlist data)
                yield_message('status', f"-> Generating content for {curator_email}..."); subject, body = generate_email_content(track, playlist, song_description)
                if not body: raise ValueError("AI generated empty body."); yield_message('status', f"-> Content generated.")
                # 2. Send Email (No change needed here)
                yield_message('status', f"-> Sending email to {curator_email}..."); send_single_email(curator_email, subject, body, sender_email, sender_password, smtp_server, smtp_port)
                yield_message('success', f"-> Email sent to {curator_email}."); sent_count += 1
                # 3. Wait (No change needed here)
                sleep_duration = 6;
                if i < total_emails_to_send - 1: yield_message('status', f"-> Waiting {sleep_duration}s..."); time.sleep(sleep_duration)
            except Exception as e:
                error_count += 1; error_msg = format_error_message(e, f"Failed for {curator_email}"); print(f"Error sending email to {curator_email}: {e}"); yield_message('error', f"-> Error: {error_msg}"); time.sleep(1)

        # --- Final Summary (No change) ---
        end_time = time.time(); duration = round(end_time - start_time); final_message = f"Finished in {duration}s. Sent: {sent_count}, Errors: {error_count} / {total_emails_to_send} attempted."
        yield_message('done', final_message)

    return Response(email_stream(), mimetype='text/event-stream')

# --- END OF FILE app/playlists/routes.py ---