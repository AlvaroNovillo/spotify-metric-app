# --- START OF (REVISED) FILE app/playlists/email.py ---

import os
import time
import smtplib
import traceback
from email.message import EmailMessage
import google.generativeai as genai
from flask import current_app

# --- Helper to Format Errors ---
def format_error_message(exception, context=""):
    """Formats an exception into a user-friendly string."""
    prefix = f"{context}: " if context else ""
    return f"{prefix}{str(exception)}"

# --- REVISED Gemini Email Content Generation with Language ---
def generate_email_template_and_preview(track_details, playlist_details, song_description, language="English"):
    """
    Generates an email body template using Gemini AI in the specified language,
    and a preview rendered with the provided initial playlist details.
    Also generates a translated subject line.
    """
    if not current_app.config.get('GEMINI_API_KEY'):
        raise ValueError("Gemini API Key is not configured.")
    if not track_details or not playlist_details or not song_description:
        raise ValueError("Missing required details for email generation.")
    if not language: language = "English"

    model_name = current_app.config.get("GEMINI_MODEL_NAME", "gemini-1.5-flash")
    print(f"[Email Gen] Using Gemini model: {model_name} to generate template in {language}")

    try:
        # Extract details for prompt context
        track_name = track_details.get('name', 'N/A')
        track_artist_name = track_details['artists'][0]['name'] if track_details.get('artists') else "Unknown Artist"
        track_url = track_details.get('external_urls', {}).get('spotify', '#')
        initial_playlist_name = playlist_details.get('name', 'Your Playlist')
        initial_curator_name = playlist_details.get('owner_name')
        if not initial_curator_name or initial_curator_name.lower() in ['n/a', 'spotify']:
            initial_curator_name = "Playlist Curator"
        signature_name = track_artist_name

        # Generate Subject based on Language
        subject_translations = {
            "Spanish": f'Propuesta Musical: "{track_name}" de {track_artist_name}',
            "French": f'Soumission Musicale : "{track_name}" par {track_artist_name}',
            "German": f'Musikvorschlag: "{track_name}" von {track_artist_name}',
            "Portuguese": f'Proposta Musical: "{track_name}" por {track_artist_name}',
            "Italian": f'Proposta Musicale: "{track_name}" di {track_artist_name}',
        }
        subject_line = subject_translations.get(language, f'Music Submission: "{track_name}" by {track_artist_name}')

        # --- Construct the Prompt for TEMPLATE Generation ---
        prompt_parts = [
            f"Generate ONLY the email body content as a template for a music submission pitch, written in {language}.",
            "Use the exact placeholders {{curator_name}} and {{playlist_name}} where the curator's name and playlist name should go.",
            "Do NOT replace these placeholders in your output.",
            "\n**Context Information (Use for tone and song details, NOT for replacing placeholders):**",
            f"*   **Example Curator's Name:** {initial_curator_name}",
            '*   **Example Playlist Name:** "' + initial_playlist_name + '"',
            f'*   **Song Title:** "{track_name}"',
            f'*   **Artist Name (of the song):** {track_artist_name}',
            f'*   **Link to the song:** {track_url}',
            f'*   **Brief Song Description (from user):** {song_description}',
            f'*   **Sender\'s Name / Signature:** {signature_name}',
            "\n**Instructions for Email Body TEMPLATE Generation:**",
            "1.  **Greeting:** Start with a friendly greeting in the target language using the placeholder `{{curator_name}}`.",
            '2.  **Context:** Briefly express appreciation for their specific playlist using the placeholder `"{playlist_name}"`.',
            f'3.  **Introduction:** Clearly introduce the song "{track_name}" by {track_artist_name} (keep names as is).',
            f'4.  **Description:** Integrate the user\'s song description: "{song_description}". Rephrase slightly for flow in the target language if needed.',
            f'5.  **Call to Action/Link:** Clearly provide the Spotify link: {track_url}. Invite them to listen.',
            "6.  **Conciseness:** Keep the template relatively short (4-7 sentences).",
            "7.  **Closing:** Use a polite closing appropriate for the target language.",
            f'8.  **Signature:** End ONLY with the provided Sender\'s Name: {signature_name}.',
            '9.  **CRITICAL:** Output ONLY the template body in the specified language. Use the exact placeholders `{{curator_name}}` and `{{playlist_name}}`. Do NOT include the subject line, greetings before the main greeting, or extra text. Start directly with "Hi {{curator_name}},".',
            f"\n---\nGENERATE THE {language.upper()} EMAIL BODY TEMPLATE NOW:",
        ]
        prompt_body = "\n".join(prompt_parts)

        # Configure Gemini model call
        model = genai.GenerativeModel(model_name)
        generation_config = genai.GenerationConfig(temperature=0.75)
        safety_settings=[ {"category": c, "threshold": "BLOCK_MEDIUM_AND_ABOVE"} for c in [ "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT" ] ]

        print(f"[Email Gen] Generating {language} email template...")
        response = model.generate_content(prompt_body, generation_config=generation_config, safety_settings=safety_settings)

        if not response.parts:
             block_reason = response.prompt_feedback.block_reason if response.prompt_feedback else 'Unknown'
             finish_reason = response.candidates[0].finish_reason if response.candidates else 'Unknown'
             raise ValueError(f"AI template generation failed or was empty. Reason: {block_reason}, Finish Reason: {finish_reason}")

        template_body = response.text.strip()
        if not template_body or '{{curator_name}}' not in template_body or '{{playlist_name}}' not in template_body:
             print(f"[Email Gen] Warning: Generated template might be missing required placeholders. Generated text:\n{template_body}")
             raise ValueError("AI failed to generate a valid template with required placeholders.")

        # --- Generate the initial PREVIEW ---
        preview_body = template_body.replace("{{curator_name}}", initial_curator_name).replace("{{playlist_name}}", initial_playlist_name)

        print(f"[Email Gen] Template and preview generated successfully.")
        return subject_line, preview_body, template_body

    except Exception as e:
        print(f"[Email Gen] Error generating {language} email template/preview: {e}")
        raise Exception(f"AI email generation failed: {str(e)}")


# --- NEW: Function to create HTML Email ---
def create_curator_outreach_html(track_details, personalized_body):
    """
    Generates a visually appealing HTML email for curator outreach.

    Args:
        track_details (dict): The full Spotify track object.
        personalized_body (str): The final, rendered email body text.

    Returns:
        str: The complete HTML email content.
    """
    track_name = track_details.get('name', 'Untitled Track')
    artist_name = track_details.get('artists', [{'name': 'Unknown Artist'}])[0]['name']
    # Try to get a 300px image, fallback to the first one available
    album_cover_url = 'https://i.imgur.com/3g2h9a3.png' # Fallback image
    if track_details.get('album', {}).get('images'):
        images = track_details['album']['images']
        cover_300 = next((img['url'] for img in images if img['height'] == 300), None)
        album_cover_url = cover_300 or images[0].get('url', album_cover_url)

    track_url = track_details.get('external_urls', {}).get('spotify', '#')
    current_year = time.strftime('%Y')

    # Convert the plain text body's newlines into HTML line breaks for display
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
                        <!-- Song Card Section -->
                        <a href="{track_url}" target="_blank" class="song-card">
                            <div class="song-image">
                                <img src="{album_cover_url}" alt="Album Art for {track_name}" style="width: 100%; max-width: 600px; border-top-left-radius: 8px; border-top-right-radius: 8px;">
                            </div>
                            <div class="song-info">
                                <h2 class="song-title" style="margin: 0 0 5px 0; font-size: 22px; font-weight: 700; color: #ffffff;">{track_name}</h2>
                                <p class="artist-name" style="margin: 0; font-size: 16px; color: #b3b3b3;">{artist_name}</p>
                            </div>
                        </a>

                        <!-- Main Content Section -->
                        <div class="main-content">
                            <p class="email-body" style="font-size: 16px; line-height: 1.6; color: #b3b3b3;">
                                {formatted_body}
                            </p>
                        </div>

                        <!-- CTA Button -->
                        <div class="cta-button-wrapper">
                            <a href="{track_url}" target="_blank" class="cta-button" style="color: #ffffff;">Listen on Spotify</a>
                        </div>

                        <!-- Footer -->
                        <div class="footer">
                            <p class="footer-text">© {current_year} {artist_name}.</p>
                        </div>
                    </div>
                </td></tr>
            </table>
        </div>
    </body>
    </html>
    """
    return html_content


# --- SMTP Email Sending (Removed - logic moved to route) ---
# The send_single_email function is no longer needed as the connection reuse logic
# in the route handles this more efficiently.

# --- END OF (REVISED) FILE app/playlists/email.py ---