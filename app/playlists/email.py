import os
import time
import smtplib
import traceback
from email.message import EmailMessage
import google.generativeai as genai
from flask import current_app # To access config like API key

# --- Helper to Format Errors ---
def format_error_message(exception, context=""):
    """Formats an exception into a user-friendly string."""
    prefix = f"{context}: " if context else ""
    # Check for specific API error attributes if needed (e.g., Gemini error codes)
    # For now, just return the string representation of the exception
    return f"{prefix}{str(exception)}"

# --- Gemini Email Content Generation ---
def generate_email_content(track_details, playlist_details, song_description):
    """
    Generates email subject and body using Gemini AI.

    Args:
        track_details (dict): Spotify track object.
        playlist_details (dict): Playlist object (from scraper).
        song_description (str): User-provided description of the song.

    Returns:
        tuple: (subject, body) strings.
        Raises Exception if generation fails.
    """
    if not current_app.config.get('GEMINI_API_KEY'):
        raise ValueError("Gemini API Key is not configured.")
    if not track_details or not playlist_details or not song_description:
        raise ValueError("Missing required details for email generation.")

    model_name = current_app.config.get("GEMINI_MODEL_NAME", "gemini-1.5-flash")
    print(f"[Email Gen] Using Gemini model: {model_name}")

    try:
        # Extract necessary details safely
        track_name = track_details.get('name', 'N/A')
        # Get the primary artist of the track
        track_artist_name = track_details['artists'][0]['name'] if track_details.get('artists') else "Unknown Artist"
        track_url = track_details.get('external_urls', {}).get('spotify', '#')

        playlist_name = playlist_details.get('name', 'Your Playlist')
        # Try to get a curator name, default to 'Playlist Curator'
        curator_name = playlist_details.get('owner_name')
        if not curator_name or curator_name.lower() in ['n/a', 'spotify']:
            curator_name = "Playlist Curator" # Generic fallback

        # Use the track's artist name for the signature
        signature_name = track_artist_name

        # --- Construct the Prompt ---
        subject_line = f'Music Submission: "{track_name}" by {track_artist_name} for playlist "{playlist_name}"'

        # Refined prompt focusing on clear instructions and desired output format
        prompt_body = f"""
        Generate ONLY the email body content for a music submission pitch.

        **Input Information:**
        *   **Curator's Name:** {curator_name}
        *   **Curator's Playlist Name:** "{playlist_name}"
        *   **Song Title:** "{track_name}"
        *   **Artist Name (of the song):** {track_artist_name}
        *   **Link to the song:** {track_url}
        *   **Brief Song Description (from user):** {song_description}
        *   **Sender's Name / Signature:** {signature_name}

        **Instructions for Email Body Generation:**
        1.  **Greeting:** Start with a personalized, friendly greeting (e.g., "Hi {curator_name}," or "Hello {curator_name},").
        2.  **Context:** Briefly express appreciation for their specific playlist, "{playlist_name}". Show you've actually looked at it (even if generically stated here).
        3.  **Introduction:** Clearly introduce the song "{track_name}" by {track_artist_name}.
        4.  **Description:** Integrate the user's song description: "{song_description}". You can slightly rephrase it for flow if needed.
        5.  **Call to Action/Link:** Clearly provide the Spotify link: {track_url}. Maybe invite them to listen.
        6.  **Conciseness:** Keep the entire email body relatively short and to the point (aim for 4-7 sentences).
        7.  **Closing:** Use a polite and standard closing (e.g., "Best regards,", "Thanks for considering,", "Sincerely,").
        8.  **Signature:** End ONLY with the provided Sender's Name: {signature_name}.
        9.  **CRITICAL:** Do NOT include the subject line. Do NOT add any extra text, placeholders like "[Your Info]", greetings before the main greeting, or explanations about the email itself. The output MUST start directly with the greeting (e.g., "Hi ...").

        **Example Structure:**

        Hi [Curator's Name],

        [Sentence expressing appreciation for their playlist "{playlist_name}".]
        I'd love to submit my track "{track_name}" by {track_artist_name} for your consideration.
        [Sentence incorporating the song description: "{song_description}".]
        You can listen to it here: {track_url}
        [Optional short sentence about fit or hope for consideration.]

        [Closing],
        {signature_name}

        ---
        GENERATE THE EMAIL BODY NOW:
        """

        # Configure Gemini model call
        model = genai.GenerativeModel(model_name)
        # Adjust generation config as needed (temperature, safety settings)
        generation_config = genai.GenerationConfig(
             temperature=0.75, # Slightly creative but not too random
             # max_output_tokens=250 # Limit output length if needed
        )
        # Standard safety settings
        safety_settings=[
             {"category": c, "threshold": "BLOCK_MEDIUM_AND_ABOVE"} for c in [
                  "HARM_CATEGORY_HARASSMENT",
                  "HARM_CATEGORY_HATE_SPEECH",
                  "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                  "HARM_CATEGORY_DANGEROUS_CONTENT"
                  ]
             ]

        print(f"[Email Gen] Generating email body for playlist '{playlist_name}'...")
        response = model.generate_content(
            prompt_body,
            generation_config=generation_config,
            safety_settings=safety_settings
        )

        # Access the generated text safely
        # Check for potential blocks due to safety or other issues
        if not response.parts:
             # Handle cases where generation might be blocked or empty
             block_reason = response.prompt_feedback.block_reason if response.prompt_feedback else 'Unknown'
             print(f"[Email Gen] Warning: Gemini generation blocked or yielded no parts. Reason: {block_reason}")
             # Try to get finish reason if available
             finish_reason = response.candidates[0].finish_reason if response.candidates else 'Unknown'
             print(f"[Email Gen] Finish Reason: {finish_reason}")

             # Depending on the reason, you might raise a specific error
             if block_reason == 'SAFETY':
                 raise ValueError("AI content generation blocked due to safety settings.")
             else:
                 raise ValueError(f"AI content generation failed or was empty. Reason: {block_reason}, Finish Reason: {finish_reason}")


        email_body = response.text.strip() # Get the text and strip whitespace
        if not email_body:
             raise ValueError("AI generated an empty email body.")

        print(f"[Email Gen] Email body generated successfully.")
        return subject_line, email_body

    except Exception as e:
        print(f"[Email Gen] Error generating email content: {e}")
        traceback.print_exc()
        # Re-raise the exception to be caught by the calling route
        raise Exception(f"AI email generation failed: {str(e)}")


# --- SMTP Email Sending ---
def send_single_email(recipient, subject, body, sender_email, sender_password, smtp_server, smtp_port):
    """
    Sends a single email using SMTP.

    Args:
        recipient (str): The recipient's email address.
        subject (str): The email subject line.
        body (str): The plain text email body.
        sender_email (str): Sender's email address (from config).
        sender_password (str): Sender's email password (from config).
        smtp_server (str): SMTP server hostname (from config).
        smtp_port (int): SMTP server port (from config).

    Raises:
        Exception on SMTP connection or sending errors.
    """
    if not all([recipient, subject, body, sender_email, sender_password, smtp_server, smtp_port]):
        raise ValueError("Missing required parameters for sending email.")

    print(f"[Email Send] Preparing to send email to: {recipient}")
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = sender_email
    msg['To'] = recipient
    # Ensure body is properly encoded (UTF-8 is standard)
    msg.set_content(body, subtype='plain', charset='utf-8')

    server = None # Initialize server to None
    try:
        # Connect to SMTP server
        print(f"[Email Send] Connecting to SMTP server: {smtp_server}:{smtp_port}")
        # Consider adding timeout to SMTP connection
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=20)
        server.ehlo() # Greet server
        # Use STARTTLS for security (common for ports 587)
        # If using port 465 (SSL), use smtplib.SMTP_SSL instead
        if smtp_port == 587:
            server.starttls()
            server.ehlo() # Re-greet after TLS
        # Login
        print(f"[Email Send] Logging in as {sender_email}...")
        server.login(sender_email, sender_password)
        print("[Email Send] SMTP login successful.")
        # Send the message
        server.send_message(msg)
        print(f"[Email Send] Email successfully sent to {recipient}.")

    except smtplib.SMTPAuthenticationError as e:
        print(f"[Email Send] SMTP Authentication Error: {e}")
        raise Exception(f"SMTP login failed for {sender_email}. Check email/password (or App Password for Gmail).") from e
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
        # Ensure server connection is closed
        if server:
            try:
                print("[Email Send] Quitting SMTP server connection.")
                server.quit()
            except smtplib.SMTPException:
                # Ignore errors during quit if connection was already problematic
                pass