# --- START OF (REVISED) FILE app/lastfm/scraper.py ---

import requests
from bs4 import BeautifulSoup
import urllib.parse
import traceback
import time
import random # Import random for delays
import re

# Use a persistent session for requests
SESSION = requests.Session()

# --- UPDATED Headers (Mimic Browser More Closely) ---
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'Accept-Language': 'en-US,en;q=0.9', # Keep English first
    'Accept-Encoding': 'gzip, deflate, br',
    'Referer': 'https://www.last.fm/', # Generic Referer
    'DNT': '1',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'same-origin',
    'Sec-Fetch-User': '?1',
})

# --- Optional Session Warm-up ---
# You can uncomment this to make an initial request, though the session
# should handle cookies on subsequent requests within the same run.
# try:
#     print("[Last.fm Scraper] Warming up session...")
#     SESSION.get("https://www.last.fm/", timeout=10)
#     print("[Last.fm Scraper] Session warm-up complete.")
# except requests.exceptions.RequestException as warmup_err:
#     print(f"[Last.fm Scraper] Warning: Session warm-up failed: {warmup_err}")


def _add_random_delay(min_sec=0.8, max_sec=2.5):
    """Adds a random delay to mimic human browsing."""
    delay = random.uniform(min_sec, max_sec)
    # print(f"    -> Waiting {delay:.2f}s...") # Optional: uncomment for debugging
    time.sleep(delay)


def scrape_all_lastfm_similar_artists_names(artist_name, max_pages=5):
    """
    Scrapes similar artists for a given artist from ALL Last.fm pages (up to max_pages).
    Includes random delays between page requests.
    """
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
            print(f"[Last.fm Scraper]  Preparing to fetch page {page}: {url}")
        except Exception as e:
            print(f"[Last.fm Scraper] Error encoding artist name '{artist_name}': {e}")
            return [] if page > 1 else None

        artists_found_on_page = 0
        try:
            _add_random_delay(1.0, 3.0) # Longer delay between page loads
            response = SESSION.get(url, timeout=20)

            if response.status_code == 404:
                print(f"[Last.fm Scraper]  Page {page} returned 404. Assuming end of results or invalid artist.")
                break

            # Explicitly check for 406 after the request, before raise_for_status
            if response.status_code == 406:
                print(f"[Last.fm Scraper]  Received 406 Not Acceptable for page {page}. Headers likely incorrect or blocked. Stopping pagination.")
                if page == 1: return None # Critical failure if first page is blocked
                else: break # Stop if a later page is blocked

            response.raise_for_status() # Raise for other errors (like 403, 5xx)
            soup = BeautifulSoup(response.text, 'html.parser')

            container = soup.find('ol', class_='similar-artists')
            if not container:
                # ... (error checking as before) ...
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

            # Extract names (logic remains the same)
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

        except requests.exceptions.RequestException as e:
            status_code = e.response.status_code if e.response is not None else 'N/A'
            print(f"[Last.fm Scraper] Request error fetching page {page} for {artist_name}: {e} (Status: {status_code})")
            # Stop for request errors other than 404/406 (handled above)
            if page == 1: return None
            else: break
        except Exception as e:
            print(f"[Last.fm Scraper] Unexpected error scraping page {page} for {artist_name}: {e}")
            traceback.print_exc()
            break

    final_list = list(unique_artist_names)
    print(f"[Last.fm Scraper] Finished scraping. Found {len(final_list)} unique similar artists total.")
    return final_list


def scrape_lastfm_upcoming_events(artist_name):
    """
    Scrapes upcoming events for a given artist from Last.fm using the table structure.
    Includes a random delay.
    """
    if not artist_name:
        print("[Last.fm Events] Error: No artist name provided.")
        return []

    try:
        encoded_artist = urllib.parse.quote_plus(artist_name)
        url = f"https://www.last.fm/music/{encoded_artist}/+events"
        print(f"[Last.fm Events] Preparing to fetch upcoming events for '{artist_name}' from: {url}")
    except Exception as e:
        print(f"[Last.fm Events] Error encoding artist name '{artist_name}': {e}")
        return []

    events = []
    try:
        _add_random_delay() # Add delay before the request
        response = SESSION.get(url, timeout=15) # Uses updated SESSION headers

        if response.status_code == 404:
             print(f"[Last.fm Events] Page not found (404) for '{artist_name}' events.")
             return []

        # Check for 406 specifically
        if response.status_code == 406:
            print(f"[Last.fm Events] Received 406 Not Acceptable for event data for '{artist_name}'. Headers might be incorrect or blocked.")
            return [] # Treat as no results found

        response.raise_for_status() # Raise for other errors
        soup = BeautifulSoup(response.text, 'html.parser')

        # (Parsing logic remains the same as previous step)
        events_section = soup.find('section', id='events-section')
        if not events_section:
             no_events_msg = soup.find(lambda tag: tag.name == "p" and "No upcoming events listed" in tag.get_text(strip=True))
             if no_events_msg: print("[Last.fm Events] Found 'no upcoming events' message.")
             else: print(f"[Last.fm Events] Could not find main events section ('section#events-section') for '{artist_name}'.")
             return []
        event_items = events_section.find_all('tr', class_='events-list-item')
        if not event_items:
             print(f"[Last.fm Events] No event rows ('tr.events-list-item') found within the section.")
             no_events_msg_section = events_section.find(lambda tag: tag.name == "p" and "No upcoming events listed" in tag.get_text(strip=True))
             if no_events_msg_section: print("[Last.fm Events] Found 'no upcoming events' message within the section.")
             return []
        print(f"[Last.fm Events] Found {len(event_items)} potential event rows.")
        for item in event_items:
            event_data = {'date': 'N/A', 'title': 'N/A', 'venue': 'N/A', 'location': 'N/A', 'url': None, 'attendees': 'N/A'}
            date_td = item.find('td', class_='events-list-item-date')
            if date_td:
                month_span = date_td.find('span', class_='events-list-item-date-icon-month')
                day_span = date_td.find('span', class_='events-list-item-date-icon-day')
                if month_span and day_span: event_data['date'] = f"{month_span.text.strip()} {day_span.text.strip()}"
                time_tag = date_td.find('time')
                if time_tag and time_tag.get('datetime'): event_data['datetime'] = time_tag.get('datetime')
            event_td = item.find('td', class_='events-list-item-event')
            if event_td:
                link_tag = event_td.find('a', class_='events-list-item-event-name')
                if link_tag:
                    title_span = link_tag.find('span', itemprop='name')
                    event_data['title'] = title_span.text.strip() if title_span else link_tag.text.strip()
                    event_data['url'] = link_tag.get('href')
                    if event_data['url'] and event_data['url'].startswith('/'): event_data['url'] = f"https://www.last.fm{event_data['url']}"
            venue_td = item.find('td', class_='events-list-item-venue')
            if venue_td:
                venue_title_div = venue_td.find('div', class_='events-list-item-venue--title')
                venue_address_div = venue_td.find('div', class_='events-list-item-venue--address')
                venue = venue_title_div.text.strip() if venue_title_div else 'N/A'
                location = venue_address_div.text.strip() if venue_address_div else 'N/A'
                event_data['venue'] = venue; event_data['location'] = location
            attendees_td = item.find('td', class_='events-list-item-attendees')
            if attendees_td:
                attendee_texts = [a.text.strip() for a in attendees_td.find_all('a')]
                event_data['attendees'] = ' · '.join(attendee_texts) if attendee_texts else attendees_td.text.strip()
            if event_data['title'] != 'N/A': events.append(event_data)
        print(f"[Last.fm Events] Successfully extracted {len(events)} upcoming events.")
        return events

    except requests.exceptions.RequestException as e:
        status_code = e.response.status_code if e.response is not None else 'N/A'
        print(f"[Last.fm Events] Request error fetching event data for {artist_name}: {e} (Status: {status_code})")
        if status_code != 404: # 406 already handled by returning []
             return None # Indicate other critical request failures
        else:
             return [] # Treat 404 as no events
    except Exception as e:
        print(f"[Last.fm Events] Unexpected error scraping events for {artist_name}: {e}")
        traceback.print_exc()
        return []


def scrape_lastfm_tags(artist_name):
    """
    Scrapes artist tags from Last.fm.
    Includes a random delay.
    """
    if not artist_name:
        print("[Last.fm Tags] Error: No artist name provided.")
        return []

    try:
        encoded_artist = urllib.parse.quote_plus(artist_name)
        url = f"https://www.last.fm/music/{encoded_artist}/+tags"
        print(f"[Last.fm Tags] Preparing to fetch tags for '{artist_name}' from: {url}")
    except Exception as e:
        print(f"[Last.fm Tags] Error encoding artist name '{artist_name}': {e}")
        return []

    tags = []
    try:
        _add_random_delay() # Add delay before the request
        response = SESSION.get(url, timeout=15) # Uses updated SESSION headers

        if response.status_code == 404:
            print(f"[Last.fm Tags] Page not found (404) for '{artist_name}' tags.")
            return []

        # Check for 406 specifically
        if response.status_code == 406:
            print(f"[Last.fm Tags] Received 406 Not Acceptable for tag data for '{artist_name}'. Headers might be incorrect or blocked.")
            return [] # Treat as no results found

        response.raise_for_status() # Raise for other errors
        soup = BeautifulSoup(response.text, 'html.parser')

        # (Parsing logic remains the same)
        tags_container = soup.find('ol', class_='big-tags')
        if not tags_container: print(f"[Last.fm Tags] Could not find tags container ('ol.big-tags') for '{artist_name}'."); return []
        tag_items = tags_container.find_all('li', class_='big-tags-item-wrap')
        if not tag_items: print(f"[Last.fm Tags] No tag list items ('li.big-tags-item-wrap') found."); return []
        print(f"[Last.fm Tags] Found {len(tag_items)} potential tag items.")
        for item in tag_items:
            tag_div = item.find('div', class_='big-tags-item')
            if not tag_div: continue
            name_tag = tag_div.find('h3', class_='big-tags-item-name')
            if name_tag:
                link_tag = name_tag.find('a', class_='link-block-target')
                if link_tag and link_tag.text:
                    tag_name = link_tag.text.strip().lower()
                    tags.append(tag_name)
        print(f"[Last.fm Tags] Successfully extracted {len(tags)} tags.")
        return tags

    except requests.exceptions.RequestException as e:
        status_code = e.response.status_code if e.response is not None else 'N/A'
        print(f"[Last.fm Tags] Request error fetching tag data for {artist_name}: {e} (Status: {status_code})")
        if status_code != 404: # 406 already handled by returning []
             return None # Indicate other critical request failures
        else:
             return [] # Treat 404 as no tags
    except Exception as e:
        print(f"[Last.fm Tags] Unexpected error scraping tags for {artist_name}: {e}")
        traceback.print_exc()
        return []

# --- END OF (REVISED) FILE app/lastfm/scraper.py ---