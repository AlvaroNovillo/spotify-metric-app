# --- START OF (IMPROVED KEYWORDS) FILE app/playlists/routes.py ---
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
import random

from . import playlists_bp
from ..spotify.auth import get_spotify_client_credentials_client
# --- MODIFICATION: Import new functions ---
from ..spotify.data import fetch_similar_artists_by_genre, fetch_release_details, fetch_spotify_details_for_names
from ..spotify.utils import parse_follower_count
from ..lastfm.scraper import scrape_lastfm_tags, scrape_all_lastfm_similar_artists_names
from .playlistsupply import login_to_playlistsupply, scrape_playlistsupply
from .email import generate_email_template_and_preview, format_error_message, create_curator_outreach_html


# --- fetch_all_artist_tracks function remains unchanged ---
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
        print(f"[FetchAllTracks] Filtered down to {len(primary_releases)} primary releases.")
        if not primary_releases:
            return {}
        full_release_details = fetch_release_details(sp_client, primary_releases)
        for release in full_release_details:
            if not release or not release.get('id'): continue
            releases_with_tracks[release['id']] = { 'id': release.get('id'), 'name': release.get('name'), 'images': release.get('images'), 'release_date': release.get('release_date'), 'tracks': [] }
            if release.get('tracks') and release['tracks'].get('items'):
                for track in release['tracks']['items']:
                    if track and track.get('id'): releases_with_tracks[release['id']]['tracks'].append(track)
        sorted_release_ids = sorted(releases_with_tracks.keys(), key=lambda r_id: releases_with_tracks[r_id].get('release_date', '0000'), reverse=True)
        sorted_releases = {r_id: releases_with_tracks[r_id] for r_id in sorted_release_ids}
        print(f"[FetchAllTracks] Found and grouped tracks for {len(sorted_releases)} unique primary releases.")
        return sorted_releases
    except spotipy.exceptions.SpotifyException as e: print(f"Spotify API error in fetch_all_artist_tracks: {e}")
    except Exception as e: print(f"An unexpected error occurred in fetch_all_artist_tracks: {e}"); traceback.print_exc()
    return {}


@playlists_bp.route('/playlist-finder/<artist_id>', methods=['GET'], endpoint='show_playlist_finder')
def playlist_finder(artist_id):
    sp = get_spotify_client_credentials_client()
    if not sp: flash('Spotify API client could not be initialized.', 'error'); return redirect(url_for('main.search_artist'))
    if not artist_id: flash('No artist ID provided for playlist finding.', 'error'); return redirect(url_for('main.search_artist'))
    try:
        artist = sp.artist(artist_id)
        if not artist: flash(f"Could not find details for artist ID {artist_id}.", 'error'); return redirect(url_for('main.search_artist'))
        artist_name = artist.get('name', 'Selected Artist'); artist_genres = artist.get('genres', [])
    except Exception as e:
        flash("An error occurred fetching artist details.", 'error'); traceback.print_exc(); return redirect(url_for('main.search_artist'))
    selected_track_id = request.args.get('selected_track_id'); user_keywords_raw = request.args.get('user_keywords', ''); search_performed = bool(selected_track_id)
    all_artist_tracks = fetch_all_artist_tracks(sp, artist_id)

    def generate_response():
        lastfm_tags = []; keywords_list = []; ps_session = None; has_scrape_error = False; global_error_message = None
        try:
            tags_result = scrape_lastfm_tags(artist_name); lastfm_tags = tags_result if tags_result is not None else []
        except Exception as e: print(f"[PlaylistFinder Stream] Error during initial tag fetch: {e}")
        yield render_template('playlist_finder_base.html', artist_id=artist_id, artist_name=artist_name, artist_genres=artist_genres, lastfm_tags=lastfm_tags, all_artist_tracks=all_artist_tracks, selected_track_id=selected_track_id, user_keywords=user_keywords_raw, search_performed=search_performed, loading=search_performed, playlists=None, global_error=None)
        if not search_performed: return
        final_playlists = {}
        try:
            if not selected_track_id: raise ValueError("Selected track ID is missing.")
            market = 'US'; selected_track = sp.track(selected_track_id, market=market)
            if not selected_track: raise ValueError(f"Track ID '{selected_track_id}' not found.")
            selected_track_name = selected_track['name']; track_artist_name = selected_track['artists'][0]['name'] if selected_track['artists'] else artist_name
            js_safe_track_name = json.dumps(selected_track_name); yield f'<script>document.title = "Searching playlists for " + {js_safe_track_name} + "...";</script>\n'
            
            # --- MODIFICATION START: Comprehensive Similar Artist and Genre Keyword Generation ---
            yield f'<script>updateProgress(10, "Finding similar artists...");</script>\n'
            print("[PlaylistFinder Stream] Building comprehensive similar artist pool for keywords...")
            
            # 1. Get artists from Spotify genres and Last.fm
            spotify_genre_artists = fetch_similar_artists_by_genre(sp, artist_id, artist_name, artist_genres)
            lastfm_names = scrape_all_lastfm_similar_artists_names(artist_name, max_pages=3) # Use fewer pages for speed
            lastfm_spotify_artists = fetch_spotify_details_for_names(sp, lastfm_names or [])
            
            # 2. Combine and de-duplicate the artist pool
            combined_artists_map = {artist['id']: artist for artist in spotify_genre_artists if artist and artist.get('id') != artist_id}
            for artist in lastfm_spotify_artists:
                if artist and artist.get('id') != artist_id: combined_artists_map[artist['id']] = artist
            similar_artists_pool = list(combined_artists_map.values())
            print(f"[PlaylistFinder Stream]  -> Created a combined pool of {len(similar_artists_pool)} unique similar artists.")

            # 3. Aggregate and find the most common genres from this new pool
            print("[PlaylistFinder Stream] Aggregating common genres from similar artists...")
            genre_counts = {}
            for artist in similar_artists_pool:
                for genre in artist.get('genres', []):
                    genre_counts[genre] = genre_counts.get(genre, 0) + 1
            sorted_genres = sorted(genre_counts.items(), key=lambda item: item[1], reverse=True)
            common_genres_from_pool = [genre for genre, count in sorted_genres[:10]] # Get top 10
            print(f"[PlaylistFinder Stream]  -> Top 10 common genres found: {common_genres_from_pool}")

            # 4. Build the final keyword set
            keywords = set()
            keywords.add(track_artist_name.lower()); keywords.add(artist_name.lower())
            for genre in artist_genres[:5]: keywords.add(genre.lower().strip()) # Source artist's genres
            for tag in lastfm_tags[:10]: keywords.add(tag.lower().strip()) # Last.fm tags
            user_kws = [kw.strip().lower() for kw in user_keywords_raw.split(',') if kw.strip()]
            for kw in user_kws: keywords.add(kw)
            for sim_artist in similar_artists_pool: keywords.add(sim_artist["name"].lower()) # Similar artist names
            for common_genre in common_genres_from_pool: keywords.add(common_genre.lower().strip()) # Common genres
            # --- MODIFICATION END ---
            
            keywords_list = sorted(list(filter(None, keywords)))
            if not keywords_list: raise ValueError("No valid keywords generated.")
            print(f"[PlaylistFinder Stream] Generated {len(keywords_list)} keywords.")
            total_keywords = len(keywords_list); js_keywords_preview = json.dumps(keywords_list[:15]); yield f'<script>updateKeywordsDisplay({js_keywords_preview});</script>\n'
            ps_user = current_app.config.get('PLAYLIST_SUPPLY_USER'); ps_pass = current_app.config.get('PLAYLIST_SUPPLY_PASS')
            if not ps_user or not ps_pass: raise ValueError("PlaylistSupply credentials missing.")
            yield f'<script>updateProgress(20, "Logging in...");</script>\n'; ps_session = login_to_playlistsupply(ps_user, ps_pass)
            if not ps_session: raise ConnectionError("Failed to log in to PlaylistSupply.")
            processed_keywords = 0; initial_progress = 25; scrape_progress_range = 70
            for keyword in keywords_list:
                processed_keywords += 1; progress = initial_progress + int((processed_keywords / total_keywords) * scrape_progress_range); js_keyword = json.dumps(keyword); yield f'<script>updateProgress({progress}, "Searching: " + {js_keyword});</script>\n'
                scrape_result = scrape_playlistsupply(keyword, ps_user, ps_session); time.sleep(0.4)
                if scrape_result is None: has_scrape_error = True; continue
                elif isinstance(scrape_result, dict) and "error" in scrape_result:
                    has_scrape_error = True; error_info = scrape_result.get("message", "Error")
                    if scrape_result.get("error") == "session_invalid": global_error_message = "PlaylistSupply Session Invalid/Expired."; break
                    continue
                elif isinstance(scrape_result, list):
                    for pl in scrape_result:
                        if isinstance(pl, dict) and pl.get('id'):
                            pl_id = pl['id']
                            if pl_id not in final_playlists: final_playlists[pl_id] = {"playlist_data": pl, "found_by": {keyword.lower()}}
                            else: final_playlists[pl_id]["found_by"].add(keyword.lower())
            if global_error_message: raise ConnectionError(global_error_message)
            sorted_playlists = []
            if final_playlists:
                yield f'<script>updateProgress(95, "Sorting results...");</script>\n'
                playlists_with_keywords = []
                for pl_id, data in final_playlists.items():
                    playlist_object = data['playlist_data']; playlist_object['found_by'] = sorted(list(data['found_by'])); playlists_with_keywords.append(playlist_object)
                sorted_playlists = sorted(playlists_with_keywords, key=lambda p: parse_follower_count(p.get('followers')) or 0, reverse=True)
            results_html = render_template('playlist_finder_results.html', selected_track_name=selected_track_name, playlists=sorted_playlists, has_scrape_error=has_scrape_error, search_performed=True, global_error=None)
            js_escaped_html = json.dumps(results_html); js_escaped_playlist_data = json.dumps(sorted_playlists)
            js_safe_final_title = json.dumps(f"Playlist Results for {selected_track_name}")
            yield f'<script>injectResultsAndData({js_escaped_html}, {js_escaped_playlist_data}); document.title = {js_safe_final_title}; hideProgress();</script>\n'
        except (ValueError, ConnectionError, spotipy.exceptions.SpotifyException) as e:
            error_occurred = str(e); js_safe_error = json.dumps(error_occurred)
            error_html = render_template('playlist_finder_results.html', playlists=None, search_performed=True, global_error=error_occurred)
            js_escaped_error_html = json.dumps(error_html)
            yield f'<script>injectResultsAndData({js_escaped_error_html}, []); hideProgress(); showSearchError("Playlist Search Error: " + {js_safe_error});</script>\n'
        except Exception as e:
            error_occurred = f"Unexpected error: {str(e)}"; traceback.print_exc(); js_safe_error = json.dumps(error_occurred)
            error_html = render_template('playlist_finder_results.html', playlists=None, search_performed=True, global_error=error_occurred)
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
    sp = get_spotify_client_credentials_client()
    if not sp:
        return Response("event: error\ndata: Spotify client error\n\n", mimetype='text/event-stream', status=503)

    sender_email = current_app.config.get("SENDER_EMAIL")
    sender_password = current_app.config.get("SENDER_PASSWORD")
    smtp_server_host = current_app.config.get("SMTP_SERVER")
    smtp_port = current_app.config.get("SMTP_PORT")

    if not all([sender_email, sender_password, smtp_server_host, smtp_port]):
        return Response("event: error\ndata: SMTP credentials missing in config\n\n", mimetype='text/event-stream', status=500)

    data = request.get_json()
    if not data:
        return Response("event: error\ndata: Missing request data\n\n", mimetype='text/event-stream', status=400)

    edited_subject = data.get('subject')
    selected_track_id = data.get('track_id')
    playlists_to_contact = data.get('playlists', [])
    email_variations = data.get('variations') 
    bcc_email = data.get('bcc_email', '').strip()

    # More robust validation: check for presence of keys, but allow playlists to be an empty list.
    if not all(k in data for k in ['subject', 'track_id', 'playlists', 'variations']):
        return Response("event: error\ndata: Missing required fields in request body\n\n", mimetype='text/event-stream', status=400)
    
    # If there are no playlists, we don't need to do anything.
    if not playlists_to_contact:
        return Response("event: status\ndata: No playlists to contact.\nevent: done\ndata: Finished. Sent: 0, Errors: 0.\n\n", mimetype='text/event-stream')
    
    def email_stream():
        track = None
        total_emails_to_send = len(playlists_to_contact)
        sent_count = 0
        error_count = 0
        start_time = time.time()
        server = None

        def yield_message(event, data):
            sanitized_data = str(data).replace('\n', ' ').replace('\r', '')
            yield f"event: {event}\ndata: {sanitized_data}\n\n"

        try:
            yield_message('status', 'Fetching track details...')
            track = sp.track(selected_track_id, market='US')
            if not track:
                raise ValueError(f"Cannot fetch track details for ID: {selected_track_id}")
            track_name = track.get('name', 'N/A')
            track_artist_name = track['artists'][0]['name'] if track.get('artists') else "Unknown Artist"
            yield_message('status', f"Track '{track_name}' details fetched.")

            yield_message('status', f"Connecting to SMTP server {smtp_server_host}:{smtp_port}...")
            try:
                if smtp_port == 465:
                     server = smtplib.SMTP_SSL(smtp_server_host, smtp_port, timeout=30)
                else:
                     server = smtplib.SMTP(smtp_server_host, smtp_port, timeout=30)
                     server.starttls()
                server.login(current_app.config.get("SMTP_LOGIN_USER") or sender_email, sender_password)
                yield_message('status', 'SMTP login successful. Starting email batch.')
            except smtplib.SMTPAuthenticationError as auth_err:
                raise ConnectionError(f"SMTP Auth Error: {auth_err}. Check credentials.") from auth_err
            except Exception as conn_err:
                raise ConnectionError(f"SMTP Connection Error: {conn_err}") from conn_err

            for i, playlist in enumerate(playlists_to_contact):
                current_status = f"({i+1}/{total_emails_to_send}) Processing '{playlist.get('name', 'N/A')}'"
                yield_message('status', current_status)
                curator_email = playlist.get('email')
                if not curator_email or '@' not in curator_email:
                    yield_message('status', f"-> Skipping (no valid email).")
                    continue

                try:
                    actual_curator_name = playlist.get('owner_name') or "Playlist Curator"
                    if actual_curator_name.lower() in ['n/a', 'spotify']: actual_curator_name = "Playlist Curator"
                
                    greeting = random.choice(email_variations['greetings'])
                    main_body = random.choice(email_variations['main_body'])
                    closing = random.choice(email_variations['closings'])
                    signature_line = random.choice(email_variations['signatures'])
                    personalized_body = "\n\n".join([greeting, main_body, closing, signature_line, track_artist_name])
                    personalized_body = personalized_body.replace("{{curator_name}}", actual_curator_name).replace("{{playlist_name}}", playlist.get('name', 'this playlist'))
                    html_body = create_curator_outreach_html(track, personalized_body)
                    
                    yield_message('status', f"-> Sending email to {curator_email}...")
                    msg = EmailMessage()
                    msg['Subject'] = edited_subject
                    msg['From'] =  f"FuzzTracks <{sender_email}>"
                    msg['To'] = curator_email
                    if bcc_email: msg['Bcc'] = bcc_email
                    msg.set_content(personalized_body)
                    msg.add_alternative(html_body, subtype='html')

                    server.send_message(msg)
                    yield_message('success', f"-> Email sent to {curator_email}.")
                    sent_count += 1

                    if i < total_emails_to_send - 1:
                        sleep_duration = random.uniform(25, 60)
                        yield_message('status', f"-> Waiting {sleep_duration:.1f}s...")
                        time.sleep(sleep_duration)

                except smtplib.SMTPException as send_err:
                    error_count += 1
                    yield_message('error', f"-> SMTP Error for {curator_email}: {send_err}")
                except Exception as e:
                    error_count += 1
                    yield_message('error', f"-> General Error for {curator_email}: {e}")

        except Exception as e:
            yield_message('error', f"Setup Failed: {e}")
        finally:
            if server:
                server.quit()
            duration = round(time.time() - start_time)
            final_message = f"Finished in {duration}s. Sent: {sent_count}, Errors: {error_count}."
            yield_message('done', final_message)

    return Response(stream_with_context(email_stream()), mimetype='text/event-stream')


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
    


# --- NEW ROUTE: To handle playlist file uploads ---
@playlists_bp.route('/upload-playlists/<artist_id>', methods=['POST'])
def upload_playlists_file(artist_id):
    if 'playlist_file' not in request.files:
        return jsonify({"error": "No file part in the request."}), 400
    
    file = request.files['playlist_file']
    if file.filename == '':
        return jsonify({"error": "No file selected."}), 400

    if file and file.filename.endswith('.xlsx'):
        try:
            df = pd.read_excel(file, engine='openpyxl')
            
            # --- Column Name Mapping (from Excel header to internal key) ---
            COLUMN_MAP = {
                'Playlist Name': 'name',
                'Spotify URL': 'url',
                'Curator Name': 'owner_name',
                'Curator Email': 'email',
                'Followers': 'followers',
                'Total Tracks': 'tracks_total',
                'Description': 'description',
                'Found By Keyword': 'found_by',
                'Contacted': 'contacted' # New column
            }
            
            # Use the inverse map for validation against the DataFrame columns
            REQUIRED_COLUMNS = ['Playlist Name', 'Spotify URL']
            if not all(col in df.columns for col in REQUIRED_COLUMNS):
                return jsonify({"error": f"Invalid Excel format. Missing required columns: {', '.join(REQUIRED_COLUMNS)}"}), 400

            # Rename columns to match internal keys
            df.rename(columns=COLUMN_MAP, inplace=True)
            
            processed_playlists = []
            for record in df.to_dict(orient='records'):
                playlist = {}
                # Populate playlist with mapped keys found in the record
                for key in COLUMN_MAP.values():
                    playlist[key] = record.get(key)

                # Extract real Spotify ID from URL
                spotify_id = None
                playlist_url = playlist.get('url')
                if playlist_url and 'open.spotify.com/playlist/' in str(playlist_url):
                    match = re.search(r'playlist/([a-zA-Z0-9]+)', str(playlist_url))
                    if match:
                        spotify_id = match.group(1)
                playlist['id'] = spotify_id or f"local_{len(processed_playlists)}"

                # Standardize the 'contacted' field to a boolean
                contacted_val = playlist.get('contacted')
                playlist['contacted'] = contacted_val == 1 or str(contacted_val).lower() == 'true'

                # Ensure 'found_by' is a list
                if isinstance(playlist.get('found_by'), str):
                     playlist['found_by'] = [kw.strip() for kw in playlist['found_by'].split(',')]
                
                processed_playlists.append(playlist)
            
            print(f"[File Upload] Successfully processed {len(processed_playlists)} playlists from uploaded file.")
            return jsonify(processed_playlists)

        except Exception as e:
            print(f"Error processing uploaded Excel file: {e}")
            traceback.print_exc()
            return jsonify({"error": f"An unexpected error occurred while processing the file: {e}"}), 500
    
    return jsonify({"error": "Invalid file type. Please upload a .xlsx file."}), 400


# --- END OF (FIXED) FILE app/playlists/routes.py ---