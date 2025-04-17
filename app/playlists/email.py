# --- START OF (CORRECTED) FILE app/playlists/email.py ---
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
    """
    if not current_app.config.get('GEMINI_API_KEY'):
        raise ValueError("Gemini API Key is not configured.")
    if not track_details or not playlist_details or not song_description:
        raise ValueError("Missing required details for email generation.")
    if not language: language = "English" # Default language

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
        subject_line = f'Music Submission: "{track_name}" by {track_artist_name}'

        # --- Construct the Prompt for TEMPLATE Generation ---
        # Use standard strings for lines containing placeholders meant for AI
        # Use f-strings only for lines inserting Python variables safely
        prompt_parts = [
            f"Generate ONLY the email body content as a template for a music submission pitch, written in {language}.",
            "Use the exact placeholders {{curator_name}} and {{playlist_name}} where the curator's name and playlist name should go.",
            "Do NOT replace these placeholders in your output.",
            "\n**Context Information (Use for tone and song details, NOT for replacing placeholders):**",
            f"*   **Example Curator's Name:** {initial_curator_name}",
            # ***** FIX: Use standard string concatenation or simple format for this line *****
            '*   **Example Playlist Name:** "' + initial_playlist_name + '"',
            f'*   **Song Title:** "{track_name}"',
            f'*   **Artist Name (of the song):** {track_artist_name}',
            f'*   **Link to the song:** {track_url}',
            f'*   **Brief Song Description (from user):** {song_description}',
            f'*   **Sender\'s Name / Signature:** {signature_name}',
            "\n**Instructions for Email Body TEMPLATE Generation:**",
            "1.  **Greeting:** Start with a friendly greeting in the target language using the placeholder `{{curator_name}}`.",
            # ***** FIX: Ensure this is NOT an f-string *****
            '2.  **Context:** Briefly express appreciation for their specific playlist using the placeholder `"{playlist_name}"`.',
            f'3.  **Introduction:** Clearly introduce the song "{track_name}" by {track_artist_name} (keep names as is).',
            f'4.  **Description:** Integrate the user\'s song description: "{song_description}". Rephrase slightly for flow in the target language if needed.',
            f'5.  **Call to Action/Link:** Clearly provide the Spotify link: {track_url}. Invite them to listen.',
            "6.  **Conciseness:** Keep the template relatively short (4-7 sentences).",
            "7.  **Closing:** Use a polite closing appropriate for the target language.",
            f'8.  **Signature:** End ONLY with the provided Sender\'s Name: {signature_name}.',
            '9.  **CRITICAL:** Output ONLY the template body in the specified language. Use the exact placeholders `{{curator_name}}` and `{{playlist_name}}`. Do NOT include the subject line, greetings before the main greeting, or extra text. Start directly with "Hi {{curator_name}},".',
            "\n**Example Template Structure:**",
            # ***** FIX: Ensure this is NOT an f-string *****
            '\nHi {{curator_name}},',
            # ***** FIX: Ensure this is NOT an f-string *****
            '\nI really enjoy your playlist "{playlist_name}"!',
            f'\nI\'d love to submit my track "{track_name}" by {track_artist_name} for your consideration.',
            f'\n[Sentence incorporating the song description: "{song_description}".]',
            f'\nYou can listen here: {track_url}',
            "\nHope you like it!",
            "\n\nBest regards,",
            f"\n{signature_name}",
            f"\n---\nGENERATE THE {language.upper()} EMAIL BODY TEMPLATE NOW:",
        ]
        prompt_body = "\n".join(prompt_parts)

        # Configure Gemini model call
        model = genai.GenerativeModel(model_name)
        generation_config = genai.GenerationConfig(temperature=0.75)
        safety_settings=[ {"category": c, "threshold": "BLOCK_MEDIUM_AND_ABOVE"} for c in [ "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT" ] ]

        print(f"[Email Gen] Generating {language} email template...")
        response = model.generate_content(prompt_body, generation_config=generation_config, safety_settings=safety_settings)

        # --- Process Response ---
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
        raise Exception(f"AI email generation failed: {str(e)}") # Re-raise cleaner exception


# --- SMTP Email Sending ---
def send_single_email(recipient, subject, body, sender_email, sender_password, smtp_server, smtp_port):
    """
    Sends a single email using SMTP. (No changes needed here)
    """
    if not all([recipient, subject, body, sender_email, sender_password, smtp_server, smtp_port]):
        raise ValueError("Missing required parameters for sending email.")

    print(f"[Email Send] Preparing to send email to: {recipient}")
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = sender_email
    msg['To'] = recipient
    msg.set_content(body, subtype='plain', charset='utf-8')

    server = None
    try:
        print(f"[Email Send] Connecting to SMTP server: {smtp_server}:{smtp_port}")
        if smtp_port == 465:
             server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=20)
        else:
             server = smtplib.SMTP(smtp_server, smtp_port, timeout=20)
             server.ehlo()
             server.starttls()
             server.ehlo()
        print(f"[Email Send] Logging in as {sender_email}...")
        server.login(sender_email, sender_password)
        print("[Email Send] SMTP login successful.")
        server.send_message(msg)
        print(f"[Email Send] Email successfully sent to {recipient}.")

    except smtplib.SMTPAuthenticationError as e:
        print(f"[Email Send] SMTP Authentication Error: {e}")
        raise Exception(f"SMTP login failed for {sender_email}. Check email/password/App Password.") from e
    except smtplib.SMTPException as e:
        print(f"[Email Send] SMTP Error: {e}")
        raise Exception(f"An SMTP error occurred while sending to {recipient}: {e}") from e
    except ConnectionRefusedError as e:
         print(f"[Email Send] Connection Refused: {e}")
         raise Exception(f"Connection refused by SMTP server {smtp_server}:{smtp_port}. Check server/port/firewall.") from e
    except TimeoutError as e:
         print(f"[Email Send] Connection Timeout: {e}")
         raise Exception(f"Connection timed out connecting to SMTP server {smtp_server}:{smtp_port}.") from e
    except Exception as e:
        print(f"[Email Send] Unexpected error sending email: {e}")
        traceback.print_exc()
        raise Exception(f"An unexpected error occurred during email sending: {e}") from e
    finally:
        if server:
            try:
                print("[Email Send] Quitting SMTP server connection.")
                server.quit()
            except smtplib.SMTPException:
                pass # Ignore errors during quit

# --- END OF (CORRECTED) FILE app/playlists/email.py ---