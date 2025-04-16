import statistics
import traceback

def parse_follower_count(follower_str):
    """
    Attempts to convert follower strings (e.g., '1,600', '5.2k', 'N/A') to integers.
    Returns None if conversion fails or input is invalid.
    """
    if not follower_str or not isinstance(follower_str, (str, int, float)):
        return None # Return None for invalid input types or empty strings

    if isinstance(follower_str, int):
        return follower_str # Already an integer
    if isinstance(follower_str, float):
        return int(follower_str) # Convert float to int

    # Process string input
    try:
        # Remove commas and whitespace, convert to lowercase
        cleaned_str = str(follower_str).replace(',', '').strip().lower()

        if not cleaned_str or cleaned_str == 'n/a':
            return None # Handle empty or "N/A" strings

        # Handle 'k' suffix for thousands
        if 'k' in cleaned_str:
            # Remove 'k' and convert to float first for cases like '5.2k'
            num_part = cleaned_str.replace('k', '')
            return int(float(num_part) * 1000)
        # Handle 'm' suffix for millions
        elif 'm' in cleaned_str:
            num_part = cleaned_str.replace('m', '')
            return int(float(num_part) * 1000000)
        # Try direct integer conversion for plain numbers
        return int(cleaned_str)
    except (ValueError, TypeError) as e:
        # Log the error and the problematic string
        # print(f"[Follower Parse] Warning: Could not parse '{follower_str}' into integer. Error: {e}")
        return None # Return None on any conversion error


def calculate_release_stats(releases):
    """
    Calculates summary statistics from a list of full release detail objects.

    Args:
        releases (list): A list of dictionary objects, each representing a full release
                         (album or single) as returned by fetch_release_details.

    Returns:
        dict: A dictionary containing calculated statistics. Returns default values if
              input is empty or invalid.
    """
    # Default stats structure
    stats = {
        'total_releases': 0,
        'album_count': 0,
        'single_count': 0,
        'compilation_count': 0, # Added for completeness
        'appears_on_count': 0,  # Added for completeness
        'average_popularity': None,
        'first_release_year': None,
        'last_release_year': None,
        'valid_popularity_count': 0 # To calculate average correctly
    }

    if not releases or not isinstance(releases, list):
        print("[Release Stats] Input is not a valid list of releases. Returning default stats.")
        return stats

    popularities = []
    release_years = []
    stats['total_releases'] = len(releases)

    for release in releases:
        if not isinstance(release, dict):
            # print("[Release Stats] Warning: Found non-dictionary item in releases list, skipping.")
            continue # Skip items that are not dictionaries

        album_type = release.get('album_type')
        if album_type == 'album':
            stats['album_count'] += 1
        elif album_type == 'single':
            stats['single_count'] += 1
        elif album_type == 'compilation':
            stats['compilation_count'] += 1
        elif album_type == 'appears_on':
             stats['appears_on_count'] += 1
        # Can add more types if needed (e.g., 'EP' if Spotify API distinguishes it clearly)

        # Process popularity
        pop = release.get('popularity')
        if isinstance(pop, int) and 0 <= pop <= 100:
            popularities.append(pop)
            stats['valid_popularity_count'] += 1
        # else: print(f"[Release Stats] Warning: Invalid or missing popularity '{pop}' for release '{release.get('name', 'N/A')}'")


        # Process release date/year
        date_str = release.get('release_date')
        if date_str and isinstance(date_str, str):
            try:
                # Handle different precisions (YYYY, YYYY-MM, YYYY-MM-DD)
                year_part = date_str.split('-')[0]
                if len(year_part) == 4: # Basic check for year format
                    year = int(year_part)
                    release_years.append(year)
                # else: print(f"[Release Stats] Warning: Unexpected year format '{year_part}' in date '{date_str}'")
            except (ValueError, IndexError, TypeError) as e:
                # print(f"[Release Stats] Warning: Could not parse year from date '{date_str}'. Error: {e}")
                pass # Ignore invalid date formats silently after warning

    # Calculate final stats
    if popularities:
        try:
            stats['average_popularity'] = round(statistics.mean(popularities), 1)
        except statistics.StatisticsError:
             print("[Release Stats] Warning: Could not calculate mean popularity.")
             stats['average_popularity'] = None # Should not happen if popularities list is not empty

    if release_years:
        try:
            stats['first_release_year'] = min(release_years)
            stats['last_release_year'] = max(release_years)
        except ValueError:
             print("[Release Stats] Warning: Could not determine min/max release year.")
             # Keep them as None if calculation fails

    # print(f"[Release Stats] Calculated stats: {stats}")
    return stats