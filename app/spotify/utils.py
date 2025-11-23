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


# === NEW AUDIO FEATURES ANALYSIS FUNCTIONS ===

def calculate_audio_feature_aggregates(tracks_with_features):
    """
    Calculate average audio features across multiple tracks.
    
    Args:
        tracks_with_features (list): List of track objects with 'audio_features' field.
    
    Returns:
        dict: Average values for each audio feature (danceability, energy, valence, etc.)
    """
    if not tracks_with_features:
        return None
    
    # Audio feature keys we want to aggregate
    feature_keys = [
        'danceability', 'energy', 'loudness', 'speechiness',
        'acousticness', 'instrumentalness', 'liveness', 'valence', 'tempo'
    ]
    
    # Collect all features
    feature_values = {key: [] for key in feature_keys}
    
    for track in tracks_with_features:
        if not track or not isinstance(track, dict):
            continue
        
        audio_f = track.get('audio_features')
        if not audio_f or not isinstance(audio_f, dict):
            continue
        
        for key in feature_keys:
            value = audio_f.get(key)
            if value is not None and isinstance(value, (int, float)):
                feature_values[key].append(value)
    
    # Calculate aggregates
    aggregates = {}
    for key, values in feature_values.items():
        if values:
            aggregates[f'{key}_avg'] = round(statistics.mean(values), 3)
            aggregates[f'{key}_min'] = round(min(values), 3)
            aggregates[f'{key}_max'] = round(max(values), 3)
        else:
            aggregates[f'{key}_avg'] = None
            aggregates[f'{key}_min'] = None
            aggregates[f'{key}_max'] = None
    
    # Add count
    aggregates['tracks_analyzed'] = len([t for t in tracks_with_features if t and t.get('audio_features')])
    
    return aggregates


def analyze_musical_signature(audio_feature_aggregates):
    """
    Identify dominant audio characteristics from aggregate features.
    
    Args:
        audio_feature_aggregates (dict): Output from calculate_audio_feature_aggregates.
    
    Returns:
        dict: Musical signature with dominant traits and descriptors.
    """
    if not audio_feature_aggregates:
        return None
    
    signature = {
        'dominant_traits': [],
        'description': []
    }
    
    # Define thresholds for "high" values (normalized 0-1 scale, except tempo and loudness)
    danceability = audio_feature_aggregates.get('danceability_avg', 0)
    energy = audio_feature_aggregates.get('energy_avg', 0)
    valence = audio_feature_aggregates.get('valence_avg', 0)
    acousticness = audio_feature_aggregates.get('acousticness_avg', 0)
    instrumentalness = audio_feature_aggregates.get('instrumentalness_avg', 0)
    speechiness = audio_feature_aggregates.get('speechiness_avg', 0)
    
    # Danceability
    if danceability and danceability > 0.7:
        signature['dominant_traits'].append('highly_danceable')
        signature['description'].append('Highly Danceable')
    elif danceability and danceability > 0.5:
        signature['dominant_traits'].append('danceable')
        signature['description'].append('Danceable')
    
    # Energy
    if energy and energy > 0.7:
        signature['dominant_traits'].append('high_energy')
        signature['description'].append('High Energy')
    elif energy and energy < 0.3:
        signature['dominant_traits'].append('low_energy')
        signature['description'].append('Calm/Mellow')
    
    # Valence (positivity)
    if valence and valence > 0.7:
        signature['dominant_traits'].append('positive')
        signature['description'].append('Upbeat/Positive')
    elif valence and valence < 0.3:
        signature['dominant_traits'].append('melancholic')
        signature['description'].append('Melancholic/Dark')
    
    # Acousticness
    if acousticness and acousticness > 0.6:
        signature['dominant_traits'].append('acoustic')
        signature['description'].append('Acoustic')
    elif acousticness and acousticness < 0.2:
        signature['dominant_traits'].append('electronic')
        signature['description'].append('Electronic/Produced')
    
    # Instrumentalness
    if instrumentalness and instrumentalness > 0.5:
        signature['dominant_traits'].append('instrumental')
        signature['description'].append('Instrumental')
    
    # Speechiness
    if speechiness and speechiness > 0.33:
        signature['dominant_traits'].append('spoken_word')
        signature['description'].append('Speech/Rap Heavy')
    
    # Create summary
    if signature['description']:
        signature['summary'] = ', '.join(signature['description'])
    else:
        signature['summary'] = 'Balanced/Mixed Sound'
    
    return signature


def calculate_advanced_artist_metrics(artist_details, releases):
    """
    Calculate advanced metrics for an artist.
    
    Args:
        artist_details (dict): Full artist object from Spotify API.
        releases (list): List of release objects.
    
    Returns:
        dict: Advanced metrics including diversity score, release velocity, etc.
    """
    metrics = {}
    
    # Genre diversity score (0-1, based on number of unique genres)
    genres = artist_details.get('genres', [])
    if genres:
        # More genres = more diverse (capped at 10 for normalization)
        metrics['genre_diversity_score'] = min(len(genres) / 10.0, 1.0)
        metrics['genre_count'] = len(genres)
    else:
        metrics['genre_diversity_score'] = 0
        metrics['genre_count'] = 0
    
    # Follower-to-popularity ratio
    followers = artist_details.get('followers', {}).get('total', 0)
    popularity = artist_details.get('popularity', 0)
    if popularity > 0:
        # Higher ratio means more followers relative to popularity
        metrics['follower_to_popularity_ratio'] = round(followers / (popularity * 1000), 2)
    else:
        metrics['follower_to_popularity_ratio'] = None
    
    # Release velocity (releases per year)
    if releases:
        release_years = []
        for release in releases:
            date_str = release.get('release_date')
            if date_str:
                try:
                    year = int(date_str.split('-')[0])
                    release_years.append(year)
                except (ValueError, IndexError):
                    pass
        
        if len(release_years) >= 2:
            year_span = max(release_years) - min(release_years) + 1
            metrics['release_velocity'] = round(len(release_years) / year_span, 2)
        else:
            metrics['release_velocity'] = len(release_years)
    else:
        metrics['release_velocity'] = 0
    
    return metrics

# === NEW ANALYTICS FUNCTIONS (Using Available API Data Only) ===

def analyze_release_patterns(releases):
    """
    Analyze release timing and frequency patterns.
    
    Args:
        releases (list): List of release objects with release_date field.
    
    Returns:
        dict: Timeline data, seasonal trends, release velocity by period.
    """
    if not releases:
        return None
    
    from collections import defaultdict
    from datetime import datetime
    
    patterns = {
        'releases_by_year': defaultdict(int),
        'releases_by_month': defaultdict(int),
        'releases_by_type': defaultdict(int),
        'labels': defaultdict(int),
        'timeline': [],
        'seasonal_pattern': None,
        'most_productive_year': None,
        'avg_time_between_releases': None
    }
    
    release_dates = []
    
    for release in releases:
        if not isinstance(release, dict):
            continue
        
        # Extract year and month
        date_str = release.get('release_date')
        if date_str:
            try:
                parts = date_str.split('-')
                year = int(parts[0])
                month = int(parts[1]) if len(parts) > 1 else 1
                
                patterns['releases_by_year'][year] += 1
                patterns['releases_by_month'][month] += 1
                
                # Store for timeline
                release_dates.append({
                    'year': year,
                    'month': month,
                    'name': release.get('name', 'Unknown'),
                    'type': release.get('album_type', 'unknown')
                })
            except (ValueError, IndexError):
                pass
        
        # Track release types
        album_type = release.get('album_type', 'unknown')
        patterns['releases_by_type'][album_type] += 1
        
        # Track labels
        label = release.get('label')
        if label:
            patterns['labels'][label] += 1
    
    # Sort timeline chronologically
    patterns['timeline'] = sorted(release_dates, key=lambda x: (x['year'], x['month']))
    
    # Find most productive year
    if patterns['releases_by_year']:
        patterns['most_productive_year'] = max(
            patterns['releases_by_year'].items(),
            key=lambda x: x[1]
        )
    
    # Determine seasonal pattern (which month is most common)
    if patterns['releases_by_month']:
        most_common_month = max(
            patterns['releases_by_month'].items(),
            key=lambda x: x[1]
        )
        month_names = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 
                      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        patterns['seasonal_pattern'] = {
            'month': most_common_month[0],
            'month_name': month_names[most_common_month[0]],
            'count': most_common_month[1]
        }
    
    # Calculate average time between releases
    if len(patterns['timeline']) >= 2:
        years = sorted(patterns['releases_by_year'].keys())
        year_span = years[-1] - years[0] + 1
        patterns['avg_time_between_releases'] = round(year_span / len(releases), 2)
    
    return patterns


def calculate_popularity_distribution(tracks):
    """
    Statistical analysis of track popularity.
    
    Args:
        tracks (list): List of track objects with popularity field.
    
    Returns:
        dict: Min, max, median, quartiles, distribution histogram data.
    """
    if not tracks:
        return None
    
    popularities = []
    for track in tracks:
        if isinstance(track, dict):
            pop = track.get('popularity')
            if isinstance(pop, (int, float)) and 0 <= pop <= 100:
                popularities.append(pop)
    
    if not popularities:
        return None
    
    popularities.sort()
    
    distribution = {
        'min': min(popularities),
        'max': max(popularities),
        'mean': round(statistics.mean(popularities), 1),
        'median': round(statistics.median(popularities), 1),
        'std_dev': round(statistics.stdev(popularities), 1) if len(popularities) > 1 else 0,
        'total_tracks': len(popularities)
    }
    
    # Calculate quartiles
    if len(popularities) >= 4:
        distribution['q1'] = round(statistics.quantiles(popularities, n=4)[0], 1)
        distribution['q3'] = round(statistics.quantiles(popularities, n=4)[2], 1)
    
    # Create histogram bins (0-20, 20-40, 40-60, 60-80, 80-100)
    bins = {'0-20': 0, '20-40': 0, '40-60': 0, '60-80': 0, '80-100': 0}
    for pop in popularities:
        if pop < 20:
            bins['0-20'] += 1
        elif pop < 40:
            bins['20-40'] += 1
        elif pop < 60:
            bins['40-60'] += 1
        elif pop < 80:
            bins['60-80'] += 1
        else:
            bins['80-100'] += 1
    
    distribution['histogram'] = bins
    
    # Calculate hit rate (tracks above 50 popularity)
    hits = sum(1 for p in popularities if p >= 50)
    distribution['hit_rate'] = round((hits / len(popularities)) * 100, 1)
    
    return distribution


def analyze_genre_evolution(releases):
    """
    Track how artist's genres change over time.
    
    Args:
        releases (list): List of release objects with genres and release_date.
    
    Returns:
        dict: Genre timeline, experimentation score.
    """
    if not releases:
        return None
    
    from collections import defaultdict
    
    evolution = {
        'genres_by_period': defaultdict(set),
        'all_genres': set(),
        'experimentation_score': 0,
        'genre_changes': []
    }
    
    # Sort releases by date
    dated_releases = []
    for release in releases:
        if isinstance(release, dict):
            date_str = release.get('release_date')
            genres = release.get('genres', [])
            if date_str and genres:
                try:
                    year = int(date_str.split('-')[0])
                    dated_releases.append({'year': year, 'genres': genres})
                except (ValueError, IndexError):
                    pass
    
    dated_releases.sort(key=lambda x: x['year'])
    
    # Group by 5-year periods
    if dated_releases:
        min_year = dated_releases[0]['year']
        max_year = dated_releases[-1]['year']
        
        for release in dated_releases:
            year = release['year']
            period = f"{(year // 5) * 5}-{((year // 5) * 5) + 4}"
            for genre in release['genres']:
                evolution['genres_by_period'][period].add(genre)
                evolution['all_genres'].add(genre)
        
        # Calculate experimentation score (unique genres / total releases)
        if len(dated_releases) > 0:
            evolution['experimentation_score'] = round(
                len(evolution['all_genres']) / len(dated_releases), 2
            )
    
    return evolution


def extract_collaborators(releases):
    """
    Find all featured artists across discography.
    
    Args:
        releases (list): List of release objects with tracks.
    
    Returns:
        dict: Collaborator list with frequency counts.
    """
    if not releases:
        return None
    
    from collections import defaultdict
    import re
    
    collaborators = defaultdict(int)
    total_tracks = 0
    tracks_with_features = 0
    
    # Common patterns for featured artists
    feat_patterns = [
        r'\(feat\.?\s+([^)]+)\)',
        r'\(ft\.?\s+([^)]+)\)',
        r'\(featuring\s+([^)]+)\)',
        r'feat\.?\s+([^-\(]+)',
        r'ft\.?\s+([^-\(]+)'
    ]
    
    for release in releases:
        if not isinstance(release, dict):
            continue
        
        tracks = release.get('tracks', {}).get('items', [])
        for track in tracks:
            if not isinstance(track, dict):
                continue
            
            total_tracks += 1
            track_name = track.get('name', '')
            
            # Try to extract featured artists from track name
            for pattern in feat_patterns:
                matches = re.findall(pattern, track_name, re.IGNORECASE)
                if matches:
                    tracks_with_features += 1
                    for match in matches:
                        # Clean up the artist name
                        artists = [a.strip() for a in match.split(',')]
                        for artist in artists:
                            if artist and len(artist) > 1:
                                collaborators[artist] += 1
                    break  # Stop after first match
            
            # Also check artists array if available
            track_artists = track.get('artists', [])
            if len(track_artists) > 1:  # More than just the main artist
                for artist in track_artists[1:]:  # Skip first (main artist)
                    if isinstance(artist, dict):
                        name = artist.get('name')
                        if name:
                            collaborators[name] += 1
    
    # Sort by frequency
    sorted_collaborators = sorted(
        collaborators.items(),
        key=lambda x: x[1],
        reverse=True
    )
    
    result = {
        'collaborators': sorted_collaborators[:20],  # Top 20
        'total_collaborators': len(collaborators),
        'total_tracks': total_tracks,
        'tracks_with_features': tracks_with_features,
        'collaboration_rate': round((tracks_with_features / total_tracks * 100), 1) if total_tracks > 0 else 0
    }
    
    return result


def calculate_career_metrics(artist_details, releases, top_tracks):
    """
    Comprehensive career analysis.
    
    Args:
        artist_details (dict): Full artist object.
        releases (list): List of release objects.
        top_tracks (list): List of top track objects.
    
    Returns:
        dict: Breakthrough moment, peak periods, consistency scores.
    """
    if not artist_details:
        return None
    
    metrics = {
        'breakthrough_release': None,
        'peak_period': None,
        'consistency_score': None,
        'longevity_years': None,
        'productivity': None,
        'evolution_score': None
    }
    
    # Calculate longevity
    if releases:
        years = []
        for release in releases:
            if isinstance(release, dict):
                date_str = release.get('release_date')
                if date_str:
                    try:
                        year = int(date_str.split('-')[0])
                        years.append(year)
                    except (ValueError, IndexError):
                        pass
        
        if years:
            metrics['longevity_years'] = max(years) - min(years) + 1
            metrics['productivity'] = round(len(releases) / metrics['longevity_years'], 2)
    
    # Find breakthrough (highest popularity release)
    if releases:
        max_pop = 0
        breakthrough = None
        for release in releases:
            if isinstance(release, dict):
                pop = release.get('popularity', 0)
                if pop > max_pop:
                    max_pop = pop
                    breakthrough = {
                        'name': release.get('name'),
                        'year': release.get('release_date', '')[:4] if release.get('release_date') else 'Unknown',
                        'popularity': pop
                    }
        metrics['breakthrough_release'] = breakthrough
    
    # Calculate consistency (std dev of release popularity)
    if releases:
        popularities = [r.get('popularity', 0) for r in releases if isinstance(r, dict) and r.get('popularity')]
        if len(popularities) > 1:
            metrics['consistency_score'] = round(100 - statistics.stdev(popularities), 1)
    
    # Evolution score (from genre diversity + collaboration rate)
    genres = artist_details.get('genres', [])
    genre_diversity = min(len(genres) / 10.0, 1.0)
    
    # Simple evolution score based on genre diversity
    metrics['evolution_score'] = round(genre_diversity * 100, 1)
    
    return metrics
