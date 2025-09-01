# --- START OF (FIXED) FILE app/playlists/routes.py ---
import os
import re
import pandas as pd
import io
import time
import json
import traceback
import smtplib
import google.generativeai as genai
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
import pandas as pd
import io
from flask import (
    render_template, redirect, url_for, flash, request, Response,
    stream_with_context, jsonify, current_app
)
import spotipy
import random # Ensure random is imported for delays

from . import playlists_bp
from ..spotify.auth import get_spotify_client_credentials_client
from ..spotify.data import fetch_similar_artists_by_genre, fetch_release_details
from ..spotify.utils import parse_follower_count
from ..lastfm.scraper import scrape_lastfm_tags, scrape_lastfm_upcoming_events
from .playlistsupply import login_to_playlistsupply, scrape_playlistsupply
from .email import generate_email_template_and_preview, format_error_message, create_curator_outreach_html


# --- REVISED FUNCTION: fetch_all_artist_tracks (Unchanged) ---
def fetch_all_artist_tracks(sp_client, artist_id):
    """
    Fetches ALL tracks for a given artist's PRIMARY releases (albums/singles)
    and groups them by release, sorted by release date.
    """
    if not sp_client or not artist_id:
        return {}

    print(f"[FetchAllTracks] Starting process for artist ID: {artist_id}")
    releases_with_tracks = {}
    all_fetched_releases = []
    try:
        # 1. Fetch all potential album/single simplified objects
        print("[FetchAllTracks] Fetching all albums/singles...")
        results = sp_client.artist_albums(artist_id, album_type='album,single', limit=50)
        all_fetched_releases.extend(results['items'])
        while results['next']:
            results = sp_client.next(results)
            all_fetched_releases.extend(results['items'])
        print(f"[FetchAllTracks] Found {len(all_fetched_releases)} total potential releases.")

        primary_releases = []
        for release in all_fetched_releases:
            if release and release.get('artists') and len(release['artists']) > 0:
                if release['artists'][0].get('id') == artist_id:
                    primary_releases.append(release)
        
        print(f"[FetchAllTracks] Filtered down to {len(primary_releases)} primary releases (artist's own albums/singles).")

        if not primary_releases:
            return {}
            
        full_release_details = fetch_release_details(sp_client, primary_releases)

        for release in full_release_details:
            if not release or not release.get('id'):
                continue

            releases_with_tracks[release['id']] = {
                'id': release.get('id'),
                'name': release.get('name'),
                'images': release.get('images'),
                'release_date': release.get('release_date'),
                'tracks': []
            }

            if release.get('tracks') and release['tracks'].get('items'):
                for track in release['tracks']['items']:
                    if track and track.get('id'):
                        releases_with_tracks[release['id']]['tracks'].append(track)
        
        sorted_release_ids = sorted(
            releases_with_tracks.keys(),
            key=lambda r_id: releases_with_tracks[r_id].get('release_date', '0000'),
            reverse=True
        )

        sorted_releases = {r_id: releases_with_tracks[r_id] for r_id in sorted_release_ids}

        print(f"[FetchAllTracks] Found and grouped tracks for {len(sorted_releases)} unique primary releases.")
        return sorted_releases

    except spotipy.exceptions.SpotifyException as e:
        print(f"Spotify API error in fetch_all_artist_tracks: {e}")
    except Exception as e:
        print(f"An unexpected error occurred in fetch_all_artist_tracks: {e}")
        traceback.print_exc()

    return {}


@playlists_bp.route('/playlist-finder/<artist_id>', methods=['GET'], endpoint='show_playlist_finder')
def playlist_finder(artist_id):
    sp = get_spotify_client_credentials_client()
    if not sp: flash('Spotify API client could not be initialized.', 'error'); return redirect(url_for('main.search_artist'))
    if not artist_id: flash('No artist ID provided for playlist finding.', 'error'); return redirect(url_for('main.search_artist'))

    try:
        print(f"[PlaylistFinder] Fetching details for artist ID: {artist_id}")
        artist = sp.artist(artist_id)
        if not artist: flash(f"Could not find details for artist ID {artist_id}.", 'error'); return redirect(url_for('main.search_artist'))
        artist_name = artist.get('name', 'Selected Artist'); artist_genres = artist.get('genres', [])
        print(f"[PlaylistFinder] Context artist: {artist_name}, Spotify Genres: {artist_genres}")
    except Exception as e:
        print(f"Error fetching artist details for playlist finder: {e}"); flash("An error occurred fetching artist details.", 'error'); traceback.print_exc(); return redirect(url_for('main.search_artist'))

    selected_track_id = request.args.get('selected_track_id'); user_keywords_raw = request.args.get('user_keywords', ''); search_performed = bool(selected_track_id)
    all_artist_tracks = fetch_all_artist_tracks(sp, artist_id)

    def generate_response():
        lastfm_tags = []
        try:
            print(f"[PlaylistFinder Stream] Fetching Last.fm tags for {artist_name}..."); tags_result = scrape_lastfm_tags(artist_name); lastfm_tags = tags_result if tags_result is not None else []
            if tags_result is None: print("[PlaylistFinder Stream]  -> Warning: Error occurred fetching Last.fm tags.")
            else: print(f"[PlaylistFinder Stream]  -> Found {len(lastfm_tags)} Last.fm tags: {lastfm_tags[:10]}")
        except Exception as e: print(f"[PlaylistFinder Stream] Error during initial data fetch (tags): {e}"); traceback.print_exc()

        yield render_template('playlist_finder_base.html', artist_id=artist_id, artist_name=artist_name, artist_genres=artist_genres, lastfm_tags=lastfm_tags, all_artist_tracks=all_artist_tracks, selected_track_id=selected_track_id, user_keywords=user_keywords_raw, search_performed=search_performed, loading=search_performed, playlists=None, global_error=None)
        if not search_performed: print("[PlaylistFinder Stream] No track selected, stopping stream."); return

        final_playlists = {}; keywords_list = []; ps_session = None; has_scrape_error = False; global_error_message = None
        try:
            print(f"[PlaylistFinder Stream] Search requested for track ID: {selected_track_id}")
            if not selected_track_id: raise ValueError("Selected track ID is missing.")
            market = 'US'; selected_track = sp.track(selected_track_id, market=market)
            if not selected_track: raise ValueError(f"Track ID '{selected_track_id}' not found.")
            selected_track_name = selected_track['name']; track_artist_name = selected_track['artists'][0]['name'] if selected_track['artists'] else artist_name
            print(f"[PlaylistFinder Stream] Selected track: '{selected_track_name}' by {track_artist_name}")
            js_safe_track_name = json.dumps(selected_track_name); yield f'<script>document.title = "Searching playlists for " + {js_safe_track_name} + "...";</script>\n'
            print("[PlaylistFinder Stream] Fetching similar genre artists for keywords..."); similar_artists = fetch_similar_artists_by_genre(sp, artist_id, artist_name, artist_genres)
            keywords = set(); keywords.add(track_artist_name.lower()); keywords.add(artist_name.lower()); keywords.add(f"{selected_track_name.lower()} {track_artist_name.lower()}")
            for genre in artist_genres[:5]: keywords.add(genre.lower().strip())
            for tag in lastfm_tags[:10]: keywords.add(tag.lower().strip())
            user_kws = [kw.strip().lower() for kw in user_keywords_raw.split(',') if kw.strip()];
            for kw in user_kws: keywords.add(kw)
            for sim_artist in similar_artists: keywords.add(sim_artist["name"].lower())
            keywords_list = sorted(list(filter(None, keywords)))
            if not keywords_list: raise ValueError("No valid keywords generated.")
            print(f"[PlaylistFinder Stream] Generated {len(keywords_list)} keywords: {keywords_list}")
            total_keywords = len(keywords_list); js_keywords_preview = json.dumps(keywords_list[:8]); yield f'<script>updateKeywordsDisplay({js_keywords_preview});</script>\n'
            ps_user = current_app.config.get('PLAYLIST_SUPPLY_USER'); ps_pass = current_app.config.get('PLAYLIST_SUPPLY_PASS')
            if not ps_user or not ps_pass: raise ValueError("PlaylistSupply credentials missing.")
            yield f'<script>updateProgress(5, "Logging in...");</script>\n'; ps_session = login_to_playlistsupply(ps_user, ps_pass)
            if not ps_session: raise ConnectionError("Failed to log in to PlaylistSupply.")
            processed_keywords = 0; initial_progress = 10; scrape_progress_range = 80
            for keyword in keywords_list:
                processed_keywords += 1; progress = initial_progress + int((processed_keywords / total_keywords) * scrape_progress_range); js_keyword = json.dumps(keyword); yield f'<script>updateProgress({progress}, "Searching: " + {js_keyword});</script>\n'
                print(f"  Scraping '{keyword}' ({processed_keywords}/{total_keywords})"); scrape_result = scrape_playlistsupply(keyword, ps_user, ps_session); time.sleep(0.4)
                if scrape_result is None: has_scrape_error = True; continue
                elif isinstance(scrape_result, dict) and "error" in scrape_result:
                    has_scrape_error = True; error_info = scrape_result.get("message", scrape_result.get("error")); print(f"  -> Scrape error for '{keyword}': {error_info}")
                    if scrape_result.get("error") == "session_invalid": global_error_message = "PlaylistSupply Session Invalid/Expired."; break
                    continue
                elif isinstance(scrape_result, list):
                    count = 0
                    for pl in scrape_result:
                        if isinstance(pl, dict) and pl.get('url') and 'open.spotify.com/playlist/' in pl['url']:
                            playlist_id = pl['id'] # Use the real ID
                            if playlist_id not in final_playlists:
                                final_playlists[playlist_id] = {"playlist_data": pl, "found_by": {keyword.lower()}}
                            else: final_playlists[playlist_id]["found_by"].add(keyword.lower())
                            count += 1
                else: print(f"  -> Unexpected scrape result type for '{keyword}': {type(scrape_result)}"); has_scrape_error = True
            if global_error_message: raise ConnectionError(global_error_message)
            sorted_playlists = []
            if final_playlists:
                yield f'<script>updateProgress(95, "Sorting results...");</script>\n'
                playlists_with_keywords = []
                for pl_id, data in final_playlists.items():
                    playlist_object = data['playlist_data']; playlist_object['found_by'] = sorted(list(data['found_by'])); playlists_with_keywords.append(playlist_object)
                sorted_playlists = sorted(playlists_with_keywords, key=lambda p: parse_follower_count(p.get('followers')) or 0, reverse=True)
            print(f"[PlaylistFinder Stream] Aggregated {len(final_playlists)} unique playlists. Sorted {len(sorted_playlists)}.")
            results_html = render_template('playlist_finder_results.html', selected_track_name=selected_track_name, playlists=sorted_playlists, has_scrape_error=has_scrape_error, search_performed=True, global_error=None)
            js_escaped_html = json.dumps(results_html); js_escaped_playlist_data = json.dumps(sorted_playlists); js_safe_final_title = json.dumps(f"Playlist Results for {selected_track_name}")
            yield f'<script>injectResultsAndData({js_escaped_html}, {js_escaped_playlist_data}); document.title = {js_safe_final_title}; hideProgress();</script>\n'
        except (ValueError, ConnectionError, spotipy.exceptions.SpotifyException) as e:
            error_occurred = str(e); print(f"[PlaylistFinder Stream] Error: {error_occurred}"); js_safe_error = json.dumps(error_occurred)
            error_html = render_template('playlist_finder_results.html', selected_track_name=selected_track_name, playlists=None, search_performed=True, global_error=error_occurred)
            js_escaped_error_html = json.dumps(error_html)
            yield f'<script>injectResultsAndData({js_escaped_error_html}, []); hideProgress(); showSearchError("Playlist Search Error: " + {js_safe_error});</script>\n'
        except Exception as e:
            error_occurred = f"Unexpected error: {str(e)}"; print(f"[PlaylistFinder Stream] Unexpected Error: {e}"); traceback.print_exc(); js_safe_error = json.dumps(error_occurred)
            error_html = render_template('playlist_finder_results.html', selected_track_name=selected_track_name, playlists=None, search_performed=True, global_error=error_occurred)
            js_escaped_error_html = json.dumps(error_html)
            yield f'<script>injectResultsAndData({js_escaped_error_html}, []); hideProgress(); showSearchError("Unexpected Error: " + {js_safe_error});</script>\n'
        finally:
            yield f'<script>hideProgress();</script>\n'
    return Response(stream_with_context(generate_response()), mimetype='text/html')


@playlists_bp.route('/generate-preview-email', methods=['POST'])
def generate_preview_email_route():
    sp = get_spotify_client_credentials_client()
    if not sp: return jsonify({"error": "Spotify API client failed to initialize."}), 503
    if not current_app.config.get('GEMINI_API_KEY'): return jsonify({"error": "AI API Key is not configured."}), 500
    data = request.get_json()
    if not data: return jsonify({"error": "Missing request data."}), 400
    track_id = data.get('track_id'); playlist = data.get('playlist'); language = data.get('language', 'English'); song_description = data.get('song_description')
    if not all([track_id, song_description, playlist, isinstance(playlist, dict)]): return jsonify({"error": "Missing required fields (track_id, song_description, playlist)."}), 400
    try:
        market = 'US'; track = sp.track(track_id, market=market)
        if not track: raise ValueError(f"Could not fetch details for track ID: {track_id}")
        subject, preview_body, template_body, variations = generate_email_template_and_preview(track, playlist, song_description, language)
        return jsonify({"subject": subject, "preview_body": preview_body, "template_body": template_body, "variations": variations})
    except (ValueError, spotipy.exceptions.SpotifyException) as e:
         error_msg = format_error_message(e, "Preview Generation Failed"); status_code = 400 if isinstance(e, ValueError) else 502
         print(f"Error generating preview/template: {error_msg}"); return jsonify({"error": error_msg}), status_code
    except Exception as e:
        print(f"Unexpected error generating email preview/template: {e}"); error_msg = format_error_message(e, "Unexpected error generating preview/template."); return jsonify({"error": error_msg}), 500
    
    
@playlists_bp.route('/send-emails', methods=['POST'])
def send_emails_route():
    # This route has no changes from the original code
    sp = get_spotify_client_credentials_client()
    if not sp: return Response("event: error\ndata: Spotify client error\n\n", mimetype='text/event-stream', status=503)
    if not current_app.config.get('GEMINI_API_KEY'): return Response("event: error\ndata: Gemini key missing\n\n", mimetype='text/event-stream', status=500)
    sender_email = current_app.config.get("SENDER_EMAIL"); sender_password = current_app.config.get("SENDER_PASSWORD"); smtp_server_host = current_app.config.get("SMTP_SERVER"); smtp_port = current_app.config.get("SMTP_PORT")
    if not all([sender_email, sender_password, smtp_server_host, smtp_port]): return Response("event: error\ndata: SMTP creds missing\n\n", mimetype='text/event-stream', status=500)

    data = request.get_json()
    if not data: return Response("event: error\ndata: Missing request data\n\n", mimetype='text/event-stream', status=400)

    edited_subject = data.get('subject')
    selected_track_id = data.get('track_id')
    playlists_to_contact = data.get('playlists', [])
    email_variations = data.get('variations')
    # --- FIX START: Remove song_description from this stage ---
    # song_description is no longer sent by the frontend for the final send action.
    # It was only needed for the generation step.
    bcc_email = data.get('bcc_email', '').strip()

    # MODIFIED: Check for the new required fields, without song_description
    if not all([edited_subject, email_variations, selected_track_id, playlists_to_contact]):
        return Response("event: error\ndata: Missing required fields (subject, variations, track, playlists)\n\n", mimetype='text/event-stream', status=400)
    # --- FIX END ---
    
    if not playlists_to_contact: return Response("event: status\ndata: No playlists provided\nevent: done\ndata: Finished.\n\n", mimetype='text/event-stream')
    if len(playlists_to_contact) > 300: playlists_to_contact = playlists_to_contact[:300]

    def email_stream():
        track = None; total_emails_to_send = len(playlists_to_contact); sent_count = 0; error_count = 0; start_time = time.time(); server = None
        def yield_message(event, data):
            sanitized_data = str(data).replace('\n', ' ').replace('\r', ''); yield f"event: {event}\ndata: {sanitized_data}\n\n"
        try:
            yield_message('status', 'Fetching track details...'); market = 'US'; track = sp.track(selected_track_id, market=market)
            if not track: raise ValueError(f"Cannot fetch track details for ID: {selected_track_id}")
            track_name = track.get('name', 'N/A'); track_artist_name = track['artists'][0]['name'] if track.get('artists') else "Unknown Artist"
            yield_message('status', f"Track '{track_name}' details fetched.")
            yield_message('status', f"Connecting to SMTP server {smtp_server_host}:{smtp_port}...")
            try:
                if smtp_port == 465: server = smtplib.SMTP_SSL(smtp_server_host, smtp_port, timeout=30)
                else: server = smtplib.SMTP(smtp_server_host, smtp_port, timeout=30); server.ehlo(); server.starttls(); server.ehlo()
                yield_message('status', 'SMTP connection established. Logging in...')
                smtp_login_user = current_app.config.get("SMTP_LOGIN_USER") or sender_email
                server.login(smtp_login_user, sender_password)
                yield_message('status', 'SMTP login successful. Starting email batch.')
            except smtplib.SMTPAuthenticationError as auth_err: raise ConnectionError(f"SMTP Auth Error: {auth_err}. Check email/password/App Password.") from auth_err
            except Exception as conn_err: raise ConnectionError(f"SMTP Connection Error: {conn_err}") from conn_err

            for i, playlist in enumerate(playlists_to_contact):
                if not isinstance(playlist, dict): yield_message('status', f"Skipping invalid playlist entry {i+1}."); continue
                playlist_name = playlist.get('name', 'N/A'); curator_email = playlist.get('email'); current_status = f"({i+1}/{total_emails_to_send}) Processing '{playlist_name}'"; yield_message('status', current_status)
                if not curator_email or '@' not in curator_email: yield_message('status', f"-> Skipping (no valid email)."); continue
                try:
                    actual_curator_name = playlist.get('owner_name') or "Playlist Curator"
                    if actual_curator_name.lower() in ['n/a', 'spotify']: actual_curator_name = "Playlist Curator"
                    greeting = random.choice(email_variations['greetings']); main_body = random.choice(email_variations['main_body']); closing = random.choice(email_variations['closings']); signature_line = random.choice(email_variations['signatures'])
                    personalized_body_parts = [greeting, main_body, closing, signature_line, track_artist_name]; personalized_body = "\n\n".join(personalized_body_parts)
                    personalized_body = personalized_body.replace("{{curator_name}}", actual_curator_name).replace("{{playlist_name}}", playlist_name)
                    html_body = create_curator_outreach_html(track, personalized_body); subject = edited_subject
                    yield_message('status', f"-> Sending email to {curator_email}...")
                    msg = EmailMessage(); msg['Subject'] = subject; msg['From'] =  f"FuzzTracks <{sender_email}>"; msg['To'] = curator_email
                    msg.set_content(personalized_body, subtype='plain'); msg.add_alternative(html_body, subtype='html')
                    recipients = [curator_email]
                    if bcc_email and '@' in bcc_email: msg['Bcc'] = bcc_email; recipients.append(bcc_email); yield_message('status', f"-> Also sending BCC to {bcc_email}")
                    server.sendmail(sender_email, recipients, msg.as_string())
                    yield_message('success', f"-> Email sent to {curator_email}."); sent_count += 1
                    sleep_duration = random.uniform(25, 60)
                    if i < total_emails_to_send - 1: yield_message('status', f"-> Waiting {sleep_duration:.1f}s..."); time.sleep(sleep_duration)
                except smtplib.SMTPException as send_err:
                    error_count += 1; error_msg = format_error_message(send_err, f"SMTP send failed for {curator_email}"); print(f"Error sending email to {curator_email}: {send_err}"); yield_message('error', f"-> Error: {error_msg}"); time.sleep(5)
                except Exception as e:
                    error_count += 1; error_msg = format_error_message(e, f"Failed processing for {curator_email}"); print(f"Error processing email for {curator_email}: {e}"); yield_message('error', f"-> Error: {error_msg}"); time.sleep(2)
        except (ValueError, ConnectionError, spotipy.exceptions.SpotifyException) as e:
            error_msg = format_error_message(e, "Email process setup failed"); print(f"Email process setup error: {e}"); yield_message('error', error_msg); yield_message('done', 'Aborted due to setup error.'); return
        except Exception as e:
            error_msg = format_error_message(e, "Unexpected error during email process"); print(f"Unexpected email process error: {e}"); traceback.print_exc(); yield_message('error', error_msg); yield_message('done', 'Aborted due to unexpected error.'); return
        finally:
            if server:
                yield_message('status', "Closing SMTP connection...")
                try: server.quit(); yield_message('status', "SMTP connection closed.")
                except smtplib.SMTPException as quit_err: print(f"Error quitting SMTP connection: {quit_err}"); yield_message('warning', "Error closing SMTP connection.")
            end_time = time.time(); duration = round(end_time - start_time); final_message = f"Finished in {duration}s. Sent: {sent_count}, Errors: {error_count} / {total_emails_to_send} attempted."; yield_message('done', final_message)
    return Response(email_stream(), mimetype='text/event-stream')


@playlists_bp.route('/filter-playlists-ai', methods=['POST'])
def filter_playlists_ai():
    sp = get_spotify_client_credentials_client()
    if not sp or not current_app.config.get('GEMINI_API_KEY'): return jsonify({"error": "Server is not configured for AI filtering."}), 500
    data = request.get_json()
    if not data: return jsonify({"error": "Missing request data."}), 400
    user_query = data.get('query'); playlists = data.get('playlists', [])
    if not user_query or not playlists: return jsonify({"error": "Missing 'query' or 'playlists' in request."}), 400
    print(f"[AI Filter V4] Received query: '{user_query}' for {len(playlists)} playlists.")
    model_name = current_app.config.get("GEMINI_MODEL_NAME", "gemini-1.5-flash"); model = genai.GenerativeModel(model_name)
    try:
        print("[AI Filter V4] Step 1: Expanding query with representative artists...")
        artist_expansion_prompt = f"""
        You are a music expert. A user wants to find playlists based on a query.
        List up to 30 representative and well-known artists for the following query.
        Focus on artists that would likely be found in playlists matching the user's intent.
        Return ONLY a JSON object with a key 'artists' which is an array of strings. Do not include any other text.
        USER QUERY: "{user_query}"
        EXAMPLE RESPONSE: {{"artists": ["Artist One", "Artist Two", "Artist Three"]}}
        YOUR JSON RESPONSE:
        """
        representative_artists = []
        try:
            artist_response = model.generate_content(artist_expansion_prompt)
            json_str = re.search(r'\{.*\}', artist_response.text, re.DOTALL).group(0)
            representative_artists = json.loads(json_str).get('artists', [])
            print(f"[AI Filter V4] AI suggested artists: {representative_artists}")
        except Exception as e:
            print(f"[AI Filter V4] Warning: Could not parse representative artists. Using query only. Error: {e}")
        print("[AI Filter V4] Step 2: Filtering playlists with augmented keywords...")
        query_words = set(re.findall(r'\b\w+\b', user_query.lower()))
        artist_names_lower = set(artist.lower() for artist in representative_artists)
        augmented_keywords = query_words.union(artist_names_lower)
        matching_playlists = []
        for pl in playlists:
            searchable_text = ' '.join([pl.get('name', '').lower(), pl.get('description', '').lower(), ' '.join(pl.get('found_by', [])).lower()])
            if any(keyword in searchable_text for keyword in augmented_keywords): matching_playlists.append(pl)
        print(f"[AI Filter V4] Found {len(matching_playlists)} potential matches.")
        sorted_playlists = sorted(matching_playlists, key=lambda p: parse_follower_count(p.get('followers', '0')) or 0, reverse=True)
        final_playlist_ids = [p['id'] for p in sorted_playlists]
        print(f"[AI Filter V4] Returning {len(final_playlist_ids)} sorted playlist IDs.")
        return jsonify({"playlist_ids": final_playlist_ids})
    except Exception as e:
        print(f"[AI Filter V4] Error during AI-augmented filtering process: {e}"); traceback.print_exc(); return jsonify({"error": f"An unexpected error occurred during AI analysis: {e}"}), 500

# --- END OF (FIXED) FILE app/playlists/routes.py ---