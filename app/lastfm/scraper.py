# --- START OF (REVISED) FILE app/lastfm/scraper.py ---

import requests
from bs4 import BeautifulSoup
import urllib.parse
import traceback
import time
import re

# (Keep SESSION and headers definition)
SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
})


# (Keep scrape_all_lastfm_similar_artists_names function as is)
def scrape_all_lastfm_similar_artists_names(artist_name, max_pages=5):
    # ... function content from previous step ...
    if not artist_name:
        print("[Last.fm Scraper] Error: No artist name provided.")
        return []

    unique_artist_names = set()
    page = 1
    print(f"[Last.fm Scraper] Starting multi-page scrape for '{artist_name}' (max {max_pages} pages)...")

    while page <= max_pages:
        try:
            encoded_artist = urllib.parse.quote_plus(artist_name)
            url = f"https://www.last.fm/music/{encoded_artist}/+similar?page={page}"
            print(f"[Last.fm Scraper]  Fetching page {page}: {url}")
        except Exception as e:
            print(f"[Last.fm Scraper] Error encoding artist name '{artist_name}': {e}")
            return [] if page > 1 else None

        artists_found_on_page = 0
        try:
            time.sleep(0.5)
            response = SESSION.get(url, timeout=20)

            if response.status_code == 404:
                print(f"[Last.fm Scraper]  Page {page} returned 404. Assuming end of results or invalid artist.")
                break

            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            container = soup.find('ol', class_='similar-artists')
            if not container:
                print(f"[Last.fm Scraper]  Could not find container 'ol.similar-artists' on page {page}.")
                if page == 1:
                     no_data_msg = soup.find(lambda tag: tag.name == "p" and "We don't have enough data" in tag.get_text())
                     if no_data_msg: print(f"[Last.fm Scraper] Found 'not enough data' message on Last.fm page.")
                     page_title = soup.find('title')
                     if page_title and "Error 404" in page_title.text: print(f"[Last.fm Scraper] Artist '{artist_name}' page not found on Last.fm (404).")
                break

            artist_items = container.find_all('li', class_='similar-artists-item-wrap', recursive=False)
            if not artist_items:
                print(f"[Last.fm Scraper]  No 'li.similar-artists-item-wrap' found on page {page}. Assuming end of results.")
                break

            for item in artist_items:
                if item.find('div', attrs={'data-ad-container': True}): continue
                artist_div = item.find('div', class_='similar-artists-item')
                if not artist_div: continue

                name_tag = artist_div.find('h3', class_='similar-artists-item-name')
                if name_tag:
                    link_tag = name_tag.find('a', class_='link-block-target')
                    if link_tag and link_tag.text:
                        similar_name = link_tag.text.strip()
                        if similar_name.lower() != artist_name.lower():
                           unique_artist_names.add(similar_name)
                           artists_found_on_page += 1

            print(f"[Last.fm Scraper]   Found {artists_found_on_page} new artists on page {page}.")
            if artists_found_on_page == 0 and page > 1:
                print(f"[Last.fm Scraper]  No new artists on page {page}, stopping pagination.")
                break

            page += 1

        except requests.exceptions.Timeout:
            print(f"[Last.fm Scraper] Error: Request timed out for {artist_name} on page {page}.")
            break
        except requests.exceptions.RequestException as e:
            status_code = e.response.status_code if e.response is not None else 'N/A'
            print(f"[Last.fm Scraper] Error fetching page {page} for {artist_name}: {e} (Status: {status_code})")
            break
        except Exception as e:
            print(f"[Last.fm Scraper] Unexpected error scraping page {page} for {artist_name}: {e}")
            traceback.print_exc()
            break

    final_list = list(unique_artist_names)
    print(f"[Last.fm Scraper] Finished scraping. Found {len(final_list)} unique similar artists total.")
    return final_list


# --- REVISED Function to Scrape Events ---
def scrape_lastfm_upcoming_events(artist_name):
    """
    Scrapes upcoming events for a given artist from Last.fm using the table structure.

    Args:
        artist_name (str): The name of the artist.

    Returns:
        list: A list of event dictionaries (date, title, venue, location, url).
              Returns empty list if no upcoming events or on error.
        None: On critical request error (timeout, connection error).
    """
    if not artist_name:
        print("[Last.fm Events] Error: No artist name provided.")
        return []

    try:
        encoded_artist = urllib.parse.quote_plus(artist_name)
        # Target the main events page first, which should show upcoming
        url = f"https://www.last.fm/music/{encoded_artist}/+events"
        print(f"[Last.fm Events] Fetching upcoming events for '{artist_name}' from: {url}")
    except Exception as e:
        print(f"[Last.fm Events] Error encoding artist name '{artist_name}': {e}")
        return []

    events = []
    try:
        time.sleep(0.3)
        response = SESSION.get(url, timeout=15)

        if response.status_code == 404:
             print(f"[Last.fm Events] Page not found (404) for '{artist_name}' events.")
             return []

        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # Find the main section containing event tables
        events_section = soup.find('section', id='events-section')
        if not events_section:
            # Check for older structure maybe? Or just no events section present
             no_events_msg = soup.find(lambda tag: tag.name == "p" and "No upcoming events listed" in tag.get_text(strip=True))
             if no_events_msg:
                  print("[Last.fm Events] Found 'no upcoming events' message.")
             else:
                  print(f"[Last.fm Events] Could not find main events section ('section#events-section') for '{artist_name}'.")
             return []

        # Find all event rows directly within the section
        # The structure seems to be <table> -> <tbody> -> <tr>
        event_items = events_section.find_all('tr', class_='events-list-item')

        if not event_items:
             print(f"[Last.fm Events] No event rows ('tr.events-list-item') found within the section.")
             # Double-check for the "no upcoming events" message specifically within the section too
             no_events_msg_section = events_section.find(lambda tag: tag.name == "p" and "No upcoming events listed" in tag.get_text(strip=True))
             if no_events_msg_section:
                 print("[Last.fm Events] Found 'no upcoming events' message within the section.")
             return []

        print(f"[Last.fm Events] Found {len(event_items)} potential event rows.")

        for item in event_items:
            event_data = {'date': 'N/A', 'title': 'N/A', 'venue': 'N/A', 'location': 'N/A', 'url': None, 'attendees': 'N/A'}

            # Date: Combine spans within the time tag
            date_td = item.find('td', class_='events-list-item-date')
            if date_td:
                month_span = date_td.find('span', class_='events-list-item-date-icon-month')
                day_span = date_td.find('span', class_='events-list-item-date-icon-day')
                if month_span and day_span:
                    event_data['date'] = f"{month_span.text.strip()} {day_span.text.strip()}"
                    # Optionally try to get the full date from attribute for sorting later
                    time_tag = date_td.find('time')
                    if time_tag and time_tag.get('datetime'):
                        event_data['datetime'] = time_tag.get('datetime')


            # Title & URL
            event_td = item.find('td', class_='events-list-item-event')
            if event_td:
                link_tag = event_td.find('a', class_='events-list-item-event-name')
                if link_tag:
                    # Find the inner span with itemprop="name" for the title
                    title_span = link_tag.find('span', itemprop='name')
                    event_data['title'] = title_span.text.strip() if title_span else link_tag.text.strip() # Fallback
                    event_data['url'] = link_tag.get('href')
                    if event_data['url'] and event_data['url'].startswith('/'):
                        event_data['url'] = f"https://www.last.fm{event_data['url']}"

            # Venue & Location
            venue_td = item.find('td', class_='events-list-item-venue')
            if venue_td:
                venue_title_div = venue_td.find('div', class_='events-list-item-venue--title')
                venue_address_div = venue_td.find('div', class_='events-list-item-venue--address')

                venue = venue_title_div.text.strip() if venue_title_div else 'N/A'
                location = venue_address_div.text.strip() if venue_address_div else 'N/A'

                event_data['venue'] = venue
                event_data['location'] = location

            # Attendees (Optional - grab combined text)
            attendees_td = item.find('td', class_='events-list-item-attendees')
            if attendees_td:
                # Combine text from all links inside
                attendee_texts = [a.text.strip() for a in attendees_td.find_all('a')]
                if attendee_texts:
                     event_data['attendees'] = ' · '.join(attendee_texts)
                else:
                    # Fallback if no links, just grab text
                    event_data['attendees'] = attendees_td.text.strip()


            # Add if title was found
            if event_data['title'] != 'N/A':
                events.append(event_data)

        print(f"[Last.fm Events] Successfully extracted {len(events)} upcoming events.")
        return events

    # --- Keep Error Handling As Is ---
    except requests.exceptions.Timeout:
        print(f"[Last.fm Events] Error: Request timed out for {artist_name}.")
        return None
    except requests.exceptions.RequestException as e:
        status_code = e.response.status_code if e.response is not None else 'N/A'
        print(f"[Last.fm Events] Error fetching event data for {artist_name}: {e} (Status: {status_code})")
        if status_code != 404: return None
        else: return []
    except Exception as e:
        print(f"[Last.fm Events] Unexpected error scraping events for {artist_name}: {e}")
        traceback.print_exc()
        return []
    

    # --- NEW Function to Scrape Tags ---
def scrape_lastfm_tags(artist_name):
    """
    Scrapes artist tags from Last.fm.

    Args:
        artist_name (str): The name of the artist.

    Returns:
        list: A list of tag names (strings). Returns empty list on error or if none found.
        None: On critical request error (timeout, connection error).
    """
    if not artist_name:
        print("[Last.fm Tags] Error: No artist name provided.")
        return []

    try:
        encoded_artist = urllib.parse.quote_plus(artist_name)
        url = f"https://www.last.fm/music/{encoded_artist}/+tags"
        print(f"[Last.fm Tags] Fetching tags for '{artist_name}' from: {url}")
    except Exception as e:
        print(f"[Last.fm Tags] Error encoding artist name '{artist_name}': {e}")
        return []

    tags = []
    try:
        time.sleep(0.3) # Be polite
        response = SESSION.get(url, timeout=15)

        # Handle 404 - Artist might exist but have no tags page, or artist doesn't exist
        if response.status_code == 404:
             print(f"[Last.fm Tags] Page not found (404) for '{artist_name}' tags.")
             return [] # Treat as no tags

        response.raise_for_status() # Raise exception for other errors
        soup = BeautifulSoup(response.text, 'html.parser')

        # Find the tags container using the provided HTML structure
        tags_container = soup.find('ol', class_='big-tags')

        if not tags_container:
            print(f"[Last.fm Tags] Could not find tags container ('ol.big-tags') for '{artist_name}'.")
            # Check for specific "no tags" messages if Last.fm has them
            return []

        # Find individual tag items
        tag_items = tags_container.find_all('li', class_='big-tags-item-wrap')

        if not tag_items:
             print(f"[Last.fm Tags] No tag list items ('li.big-tags-item-wrap') found.")
             return []

        print(f"[Last.fm Tags] Found {len(tag_items)} potential tag items.")

        for item in tag_items:
            tag_div = item.find('div', class_='big-tags-item')
            if not tag_div: continue

            name_tag = tag_div.find('h3', class_='big-tags-item-name')
            if name_tag:
                link_tag = name_tag.find('a', class_='link-block-target')
                if link_tag and link_tag.text:
                    tag_name = link_tag.text.strip().lower() # Get tag name and lowercase it
                    tags.append(tag_name)

        print(f"[Last.fm Tags] Successfully extracted {len(tags)} tags.")
        return tags

    except requests.exceptions.Timeout:
        print(f"[Last.fm Tags] Error: Request timed out for {artist_name}.")
        return None
    except requests.exceptions.RequestException as e:
        status_code = e.response.status_code if e.response is not None else 'N/A'
        print(f"[Last.fm Tags] Error fetching tag data for {artist_name}: {e} (Status: {status_code})")
        if status_code != 404: return None
        else: return []
    except Exception as e:
        print(f"[Last.fm Tags] Unexpected error scraping tags for {artist_name}: {e}")
        traceback.print_exc()
        return []

# --- END OF (REVISED) FILE app/lastfm/scraper.py ---

# --- END OF (REVISED) FILE app/lastfm/scraper.py ---