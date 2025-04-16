# --- START OF (REVISED) FILE app/lastfm/scraper.py ---

import requests
from bs4 import BeautifulSoup
import urllib.parse
import traceback
import time
import re

# Use a persistent session for requests
SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
})

def scrape_all_lastfm_similar_artists_names(artist_name, max_pages=9):
    """
    Scrapes similar artists for a given artist from ALL Last.fm pages (up to max_pages).

    Args:
        artist_name (str): The name of the artist to search for.
        max_pages (int): Maximum number of pagination pages to scrape.

    Returns:
        list: A list of unique similar artist names found. Returns empty list on error.
        None: On critical initial request errors (timeout, connection error on page 1).
    """
    if not artist_name:
        print("[Last.fm Scraper] Error: No artist name provided.")
        return []

    unique_artist_names = set()
    page = 1
    print(f"[Last.fm Scraper] Starting multi-page scrape for '{artist_name}' (max {max_pages} pages)...")

    while page <= max_pages:
        # Prepare the URL for the current page
        try:
            encoded_artist = urllib.parse.quote_plus(artist_name)
            url = f"https://www.last.fm/music/{encoded_artist}/+similar?page={page}"
            print(f"[Last.fm Scraper]  Fetching page {page}: {url}")
        except Exception as e:
            print(f"[Last.fm Scraper] Error encoding artist name '{artist_name}': {e}")
            # If encoding fails, we can't proceed
            return [] if page > 1 else None # Return empty if already found some, None if first page failed

        artists_found_on_page = 0
        try:
            # Add a delay to be polite
            time.sleep(0.5) # Increase delay slightly for multi-page
            response = SESSION.get(url, timeout=20) # Increase timeout slightly

            # Check for 404 specifically - indicates end of results or bad artist name
            if response.status_code == 404:
                print(f"[Last.fm Scraper]  Page {page} returned 404. Assuming end of results or invalid artist.")
                break # Stop pagination

            response.raise_for_status() # Raise exception for other bad status codes

            soup = BeautifulSoup(response.text, 'html.parser')

            # Find the container
            container = soup.find('ol', class_='similar-artists')
            if not container:
                print(f"[Last.fm Scraper]  Could not find container 'ol.similar-artists' on page {page}.")
                if page == 1: # If container missing on page 1, it's likely an issue
                     no_data_msg = soup.find(lambda tag: tag.name == "p" and "We don't have enough data" in tag.get_text())
                     if no_data_msg: print(f"[Last.fm Scraper] Found 'not enough data' message on Last.fm page.")
                     page_title = soup.find('title')
                     if page_title and "Error 404" in page_title.text: print(f"[Last.fm Scraper] Artist '{artist_name}' page not found on Last.fm (404).")
                break # Stop if container not found on subsequent pages

            # Find all artist items
            artist_items = container.find_all('li', class_='similar-artists-item-wrap', recursive=False)
            if not artist_items:
                print(f"[Last.fm Scraper]  No 'li.similar-artists-item-wrap' found on page {page}. Assuming end of results.")
                break # Stop if no artists found on the page

            # Extract names
            for item in artist_items:
                if item.find('div', attrs={'data-ad-container': True}): continue # Skip ads
                artist_div = item.find('div', class_='similar-artists-item')
                if not artist_div: continue

                name_tag = artist_div.find('h3', class_='similar-artists-item-name')
                if name_tag:
                    link_tag = name_tag.find('a', class_='link-block-target')
                    if link_tag and link_tag.text:
                        similar_name = link_tag.text.strip()
                        if similar_name.lower() != artist_name.lower(): # Exclude self
                           unique_artist_names.add(similar_name)
                           artists_found_on_page += 1

            print(f"[Last.fm Scraper]   Found {artists_found_on_page} new artists on page {page}.")
            if artists_found_on_page == 0 and page > 1: # If a later page has 0 items, stop
                print(f"[Last.fm Scraper]  No new artists on page {page}, stopping pagination.")
                break

            page += 1 # Go to the next page

        except requests.exceptions.Timeout:
            print(f"[Last.fm Scraper] Error: Request timed out for {artist_name} on page {page}.")
            # Decide whether to stop or continue (returning only found names)
            break # Stop on timeout for now
        except requests.exceptions.RequestException as e:
            status_code = e.response.status_code if e.response is not None else 'N/A'
            print(f"[Last.fm Scraper] Error fetching page {page} for {artist_name}: {e} (Status: {status_code})")
            # Stop on request errors
            break
        except Exception as e:
            print(f"[Last.fm Scraper] Unexpected error scraping page {page} for {artist_name}: {e}")
            traceback.print_exc()
            # Stop on parsing errors
            break

    final_list = list(unique_artist_names)
    print(f"[Last.fm Scraper] Finished scraping. Found {len(final_list)} unique similar artists total.")
    return final_list # Return the list of unique names found across pages

# --- END OF (REVISED) FILE app/lastfm/scraper.py ---