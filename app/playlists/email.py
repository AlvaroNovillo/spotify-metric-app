# --- START OF (FIXED) FILE app/playlists/email.py ---

import os
import time
import smtplib
import traceback
import json
import random
from email.message import EmailMessage
import google.generativeai as genai
from flask import current_app

# --- Helper to Format Errors (Unchanged) ---
def format_error_message(exception, context=""):
    """Formats an exception into a user-friendly string."""
    prefix = f"{context}: " if context else ""
    return f"{prefix}{str(exception)}"

# --- UPGRADED Gemini Email Content Generation with Variations (FIXED) ---
def generate_email_template_and_preview(track_details, playlist_details, song_description, language="English"):
    """
    Generates a structured JSON object with multiple email variations (spintax)
    using Gemini AI, and creates both an editable template and a personalized preview.
    """
    if not current_app.config.get('GEMINI_API_KEY'):
        raise ValueError("Gemini API Key is not configured.")
    if not all([track_details, playlist_details, song_description]):
        raise ValueError("Missing required details for email generation.")
    if not language: language = "English"

    model_name = current_app.config.get("GEMINI_MODEL_NAME", "gemini-1.5-flash")
    print(f"[Email Gen] Using Gemini model: {model_name} to generate VARIATIONS in {language}")

    try:
        # Context details (Unchanged)
        track_name = track_details.get('name', 'N/A')
        track_artist_name = track_details['artists'][0]['name'] if track_details.get('artists') else "Unknown Artist"
        track_url = track_details.get('external_urls', {}).get('spotify', '#')
        initial_playlist_name = playlist_details.get('name', 'Your Playlist')
        initial_curator_name = playlist_details.get('owner_name')
        if not initial_curator_name or initial_curator_name.lower() in ['n/a', 'spotify']:
            initial_curator_name = "Playlist Curator"
        signature_name = track_artist_name
        
        # Subject Line (Unchanged)
        subject_translations = {
            "Spanish": f'Propuesta Musical: "{track_name}" de {track_artist_name}',
            "French": f'Soumission Musicale : "{track_name}" par {track_artist_name}',
            "German": f'Musikvorschlag: "{track_name}" von {track_artist_name}',
            "Portuguese": f'Proposta Musical: "{track_name}" por {track_artist_name}',
            "Italian": f'Proposta Musicale: "{track_name}" di {track_artist_name}',
        }
        subject_line = subject_translations.get(language, f'Music Submission: "{track_name}" by {track_artist_name}')

        # --- FIX START: Modified prompt to prevent the AI from adding the link ---
        prompt_parts = [
            f"You are a professional music promoter. Generate a JSON object for an email pitch written in {language}.",
            "The JSON object must contain keys for 'greetings', 'main_body', 'closings', and 'signatures'.",
            "Each key must have an array of at least 4 unique, professionally-toned string variations.",
            "The placeholders {{curator_name}} and {{playlist_name}} MUST be used exactly as written.",
            "\n**Context for Content:**",
            f"*   **Song:** \"{track_name}\" by {track_artist_name}",
            f"*   **User's Song Description:** {song_description}",
            # Note: The track_url is no longer passed in the prompt context to avoid confusion.
            "\n**JSON Structure Requirements:**",
            "*   `greetings`: Variations for the opening line (e.g., 'Hi {{curator_name}},').",
            "*   `main_body`: Variations for the main email body. Each variation MUST:",
            "    1. Mention the curator's playlist using the `{{playlist_name}}` placeholder.",
            "    2. Seamlessly and creatively integrate the user's song description.",
            "    3. End with a sentence inviting the curator to listen (e.g., 'I'd love for you to give it a listen.').",
            "    4. CRITICAL: Do NOT include the song's URL or any hyperlinks in this part. The link will be added separately.", # Explicit instruction
            "*   `closings`: Variations for the closing sentence before the signature.",
            "*   `signatures`: Variations for the sign-off (e.g., 'Best regards,').",
            "\n**CRITICAL:** Respond with ONLY the raw JSON object. Do not include markdown, explanations, or any text outside the JSON object.",
            f"\n---\nGENERATE THE {language.upper()} JSON NOW:",
        ]
        # --- FIX END ---
        prompt_body = "\n".join(prompt_parts)
        
        # Model Call (Unchanged)
        model = genai.GenerativeModel(model_name)
        generation_config = genai.GenerationConfig(temperature=0.8)
        safety_settings=[ {"category": c, "threshold": "BLOCK_MEDIUM_AND_ABOVE"} for c in [ "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT" ] ]

        print(f"[Email Gen] Generating {language} email variations...")
        response = model.generate_content(prompt_body, generation_config=generation_config, safety_settings=safety_settings)

        if not response.parts:
             raise ValueError(f"AI generation failed or was empty. Reason: {response.prompt_feedback.block_reason if response.prompt_feedback else 'Unknown'}")
        
        json_text = response.text.strip().replace("```json", "").replace("```", "")
        email_variations = json.loads(json_text)

        required_keys = ['greetings', 'main_body', 'closings', 'signatures']
        if not all(key in email_variations and isinstance(email_variations[key], list) and email_variations[key] for key in required_keys):
            raise ValueError("AI did not return the expected JSON structure with a 'main_body'.")
            
        # --- FIX START: Manually construct the email body with a controlled link ---

        # 1. Create a clear, separate call-to-action line with the link.
        cta_line_translations = {
            "Spanish": f"Puedes escuchar la canción aquí: {track_url}",
            "French": f"Vous pouvez écouter le morceau ici : {track_url}",
            "German": f"Sie können den Titel hier anhören: {track_url}",
            "Portuguese": f"Você pode ouvir a faixa aqui: {track_url}",
            "Italian": f"Puoi ascoltare il brano qui: {track_url}",
        }
        call_to_action_line = cta_line_translations.get(language, f"You can listen to the track here: {track_url}")

        # 2. Build the base template string for the user to edit
        template_body_parts = [
            email_variations['greetings'][0],
            email_variations['main_body'][0], # AI generates the pitch without the link.
            call_to_action_line,              # We add our controlled CTA line.
            email_variations['closings'][0],
            email_variations['signatures'][0],
            signature_name
        ]
        template_body = "\n\n".join(template_body_parts)

        # 3. Build the preview body by personalizing the template
        preview_body = template_body.replace("{{curator_name}}", initial_curator_name).replace("{{playlist_name}}", initial_playlist_name)
        
        # --- FIX END ---
        
        print("[Email Gen] Template, variations, and preview generated successfully.")
        # Return all necessary pieces of information
        return subject_line, preview_body, template_body, email_variations

    except Exception as e:
        print(f"[Email Gen] Error during email generation: {e}")
        traceback.print_exc()
        # Re-raise to be caught by the route
        raise Exception(f"AI email generation failed: {str(e)}")


# --- create_curator_outreach_html (This function is unchanged) ---
def create_curator_outreach_html(track_details, personalized_body):
    # This function remains exactly the same as before.
    track_name = track_details.get('name', 'Untitled Track')
    artist_name = track_details.get('artists', [{'name': 'Unknown Artist'}])[0]['name']
    album_cover_url = 'https://i.imgur.com/3g2h9a3.png'
    if track_details.get('album', {}).get('images'):
        images = track_details['album']['images']
        # Prioritize 300px image, fallback to first, then to placeholder
        cover_300 = next((img['url'] for img in images if img['height'] == 300), None)
        album_cover_url = cover_300 or images[0].get('url', album_cover_url) if images else album_cover_url
    track_url = track_details.get('external_urls', {}).get('spotify', '#')
    current_year = time.strftime('%Y')
    # Convert newlines to <br> for HTML rendering
    formatted_body = personalized_body.replace('\n', '<br>')
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Music Submission: {track_name}</title>
    <style>
        body, table, td, a {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
        table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
        img {{ -ms-interpolation-mode: bicubic; border: 0; height: auto; line-height: 100%; outline: none; text-decoration: none; }}
        body {{ margin: 0; padding: 0; background-color: #121212; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif, 'Apple Color Emoji', 'Segoe UI Emoji', 'Segoe UI Symbol'; }}
        .wrapper {{ width: 100%; background-color: #121212; }}
        .container {{ width: 100%; max-width: 600px; margin: 0 auto; background-color: #1E1E1E; border-radius: 8px; overflow: hidden; border: 1px solid #2d2d2d; }}
        .song-card {{ display: block; background-color: #282828; border-radius: 8px; margin: 0 30px 30px 30px; text-decoration: none; }}
        .song-image img {{ width: 100%; max-width: 600px; height: auto; border-top-left-radius: 8px; border-top-right-radius: 8px; display: block; }}
        .song-info {{ padding: 20px; text-align: left; }}
        .song-title {{ margin: 0 0 5px 0; font-size: 22px; font-weight: 700; color: #ffffff; }}
        .artist-name {{ margin: 0; font-size: 16px; color: #b3b3b3; }}
        .main-content {{ padding: 0 30px 30px 30px; }}
        .email-body {{ font-size: 16px; line-height: 1.6; color: #b3b3b3; }}
        .cta-button-wrapper {{ text-align: center; margin: 30px 0; }}
        .cta-button {{ display: inline-block; padding: 14px 35px; background-color: #1DB954; color: #ffffff; text-decoration: none; font-size: 16px; font-weight: bold; border-radius: 50px; }}
        .footer {{ padding: 20px 30px; text-align: center; background-color: #181818; }}
        .footer-text {{ margin: 0; font-size: 12px; color: #777777; }}
    </style>
    </head>
    <body style="margin: 0; padding: 0; background-color: #121212;">
        <div class="wrapper">
            <table border="0" cellpadding="0" cellspacing="0" width="100%">
                <tr><td align="center" style="padding: 20px 10px;">
                    <div class="container">
                        <a href="{track_url}" target="_blank" class="song-card">
                            <div class="song-image">
                                <img src="{album_cover_url}" alt="Album Art for {track_name}" style="width: 100%; max-width: 600px; border-top-left-radius: 8px; border-top-right-radius: 8px;">
                            </div>
                            <div class="song-info">
                                <h2 class="song-title" style="margin: 0 0 5px 0; font-size: 22px; font-weight: 700; color: #ffffff;">{track_name}</h2>
                                <p class="artist-name" style="margin: 0; font-size: 16px; color: #b3b3b3;">{artist_name}</p>
                            </div>
                        </a>
                        <div class="main-content">
                            <p class="email-body" style="font-size: 16px; line-height: 1.6; color: #b3b3b3;">
                                {formatted_body}
                            </p>
                        </div>
                        <div class="cta-button-wrapper">
                            <a href="{track_url}" target="_blank" class="cta-button" style="color: #ffffff;">Listen on Spotify</a>
                        </div>
                        <div class="footer">
                            <p class="footer-text">© {current_year} Fuzztracks.</p>
                        </div>
                    </div>
                </td></tr>
            </table>
        </div>
    </body>
    </html>
    """
    return html_content