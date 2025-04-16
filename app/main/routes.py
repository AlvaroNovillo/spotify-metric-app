# --- START OF FILE app/main/routes.py ---
import time
import traceback
from flask import (
    render_template, redirect, url_for, flash, request, current_app
)
import spotipy

from . import main_bp
# Import the NEW client credentials function
from ..spotify.auth import get_spotify_client_credentials_client
from ..spotify.data import fetch_release_details, fetch_similar_genre_artists_data
from ..spotify.utils import calculate_release_stats


# --- Core Routes ---

@main_bp.route('/')
def index():
    # Redirect root to the search page
    return redirect(url_for('main.search_artist'))

# Removed /login, /callback, /logout routes

# --- Artist/Data Display Routes ---

# Removed artist_dashboard route

@main_bp.route('/search', methods=['GET'])
def search_artist():
    # Use Client Credentials client - no user login needed
    sp = get_spotify_client_credentials_client()
    if not sp:
        # If client fails (e.g., missing creds), show an error on the search page
        flash('Spotify API client could not be initialized. Please check application configuration.', 'error')
        # Render the search template without results, allowing user to see the error
        return render_template('search.html', query='', search_performed=False, main_artist=None, main_artist_releases=[], top_tracks=[], release_stats={})

    query = request.args.get('query', '').strip()
    search_performed = bool(query)
    main_artist_details = None
    main_artist_releases_data = []
    top_tracks_data = []
    release_stats = {}

    if search_performed:
        print(f"Performing artist search for: '{query}'")
        try:
            results = sp.search(q=query, type='artist', limit=1)

            if results and results['artists']['items']:
                # Artist found, fetch details
                artist_id = results['artists']['items'][0]['id']
                artist_name = results['artists']['items'][0]['name']
                print(f"Found artist: {artist_name} ({artist_id}). Fetching details...")
                main_artist_details = sp.artist(artist_id) # Fetch full artist details

                # Fetch Top Tracks (Market 'US' as default, no user context)
                print(f"Fetching top tracks for {artist_name}...")
                try:
                    market = 'US' # Define a default market
                    top_tracks_result = sp.artist_top_tracks(artist_id, country=market)
                    if top_tracks_result and top_tracks_result.get('tracks'):
                        top_tracks_data = top_tracks_result['tracks']
                        print(f"  Found {len(top_tracks_data)} top tracks in market {market}.")
                    else:
                        print(f"  No public top tracks found for {artist_name} in market {market}.")
                except spotipy.exceptions.SpotifyException as e:
                    print(f"  Spotify API error fetching top tracks for search result: {e}")
                    flash(f"Could not fetch top tracks: {e.msg}", "warning")
                except Exception as e:
                    print(f"  Unexpected error fetching top tracks for search result: {e}")
                    traceback.print_exc()
                    flash("An unexpected error occurred fetching top tracks.", "warning")

                # Fetch Releases
                print(f"Fetching releases for {artist_name}...")
                simplified_releases = []
                try:
                    simplified_releases_result = sp.artist_albums(artist_id, album_type='album,single', limit=20)
                    if simplified_releases_result and simplified_releases_result['items']:
                        simplified_releases = simplified_releases_result['items']
                        print(f"Found {len(simplified_releases)} releases. Fetching details...")
                        # Fetch Full Details (pass the client explicitly)
                        main_artist_releases_data = fetch_release_details(sp, simplified_releases)
                        # Calculate Stats
                        release_stats = calculate_release_stats(main_artist_releases_data)
                    else:
                        print(f"No primary releases found for {artist_name}.")
                        # flash(f'No recent albums or singles found for {artist_name}.', 'info') # Optional message
                except spotipy.exceptions.SpotifyException as e:
                    print(f"  Spotify API error fetching releases for search result: {e}")
                    flash(f"Could not fetch releases: {e.msg}", "warning")
                except Exception as e:
                    print(f"  Unexpected error fetching releases for search result: {e}")
                    traceback.print_exc()
                    flash("An unexpected error occurred fetching releases.", "warning")

            else:
                print(f"No artist found matching '{query}'.")
                flash(f'No artist found matching "{query}". Please try a different name.', 'warning')

        except spotipy.exceptions.SpotifyException as e:
            print(f"Spotify API error during search: {e}")
            # Client Credentials flow doesn't usually raise 401 unless creds are wrong
            flash(f'A Spotify error occurred during the search: {e.msg}. Check API Credentials.', 'error')
            main_artist_details = None; main_artist_releases_data = []; top_tracks_data = []; release_stats = {}
        except Exception as e:
            print(f"Unexpected error during artist search: {e}")
            flash('An unexpected error occurred during the search.', 'error')
            traceback.print_exc()
            main_artist_details = None; main_artist_releases_data = []; top_tracks_data = []; release_stats = {}

    # Render the search template, which now includes the results display logic
    # The template will use the '_artist_display.html' partial if 'main_artist' exists
    return render_template('search.html',
                           query=query,
                           search_performed=search_performed,
                           main_artist=main_artist_details,
                           main_artist_releases=main_artist_releases_data,
                           top_tracks=top_tracks_data,
                           release_stats=release_stats)


@main_bp.route('/similar-genre-artists/<artist_id>')
def similar_genre_artists(artist_id):
    sp = get_spotify_client_credentials_client()
    if not sp:
        flash('Spotify API client could not be initialized.', 'error')
        # Redirect back to search or show an error page? Redirecting for now.
        return redirect(url_for('main.search_artist'))

    if not artist_id:
        flash('No artist ID provided for finding similar artists.', 'error')
        return redirect(url_for('main.search_artist'))

    # Fetch the details of the artist we're basing the search on
    try:
        print(f"Fetching details for artist ID {artist_id} to find similar artists...")
        source_artist = sp.artist(artist_id)
        if not source_artist:
            flash(f"Could not find details for artist ID {artist_id}.", 'error')
            return redirect(url_for('main.search_artist'))

        source_artist_name = source_artist.get('name', 'Selected Artist')
        source_artist_genres = source_artist.get('genres', [])
        print(f"Source artist: {source_artist_name}, Genres: {source_artist_genres}")

        if not source_artist_genres:
            flash(f'No genres found for {source_artist_name}. Cannot find similar artists by genre.', 'info')
            # Pass the source artist name for context even if no results
            return render_template('similar_genre.html', artists=[], main_artist_name=source_artist_name, search_genres=[])

        # Use helper function to get similar artists' data
        genres_to_search = source_artist_genres[:2] # Use top 2 genres
        similar_artists_data = fetch_similar_genre_artists_data(
            sp, artist_id, source_artist_name, genres_to_search, limit=24
        )

        if not similar_artists_data:
            flash(f'Could not find other artists based on the genres: {", ".join(genres_to_search)}.', 'info')

        return render_template('similar_genre.html',
                               artists=similar_artists_data,
                               main_artist_name=source_artist_name, # Display the name of the artist searched for
                               search_genres=genres_to_search,
                               source_artist_id=artist_id) # Pass ID for back button maybe

    except spotipy.exceptions.SpotifyException as e:
        print(f"Spotify API error fetching similar artists: {e}")
        flash(f'A Spotify error occurred: {e.msg}', 'error')
        # Attempt to render template with error context if possible
        source_name = source_artist.get('name', 'the selected artist') if 'source_artist' in locals() else 'the artist'
        return render_template('similar_genre.html', artists=[], main_artist_name=source_name, search_genres=[], source_artist_id=artist_id)
    except Exception as e:
        print(f"Unexpected error fetching similar artists: {e}")
        flash('An unexpected error occurred while finding similar artists.', 'error')
        traceback.print_exc()
        source_name = source_artist.get('name', 'the selected artist') if 'source_artist' in locals() else 'the artist'
        return render_template('similar_genre.html', artists=[], main_artist_name=source_name, search_genres=[], source_artist_id=artist_id)

# --- END OF FILE app/main/routes.py ---