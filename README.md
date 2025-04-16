# Spotify Artist Metrics Dashboard 🎵

## Overview
This project is a Flask web application that allows a Spotify artist to log in with their Spotify account and visualize valuable metrics about:

- Their own Spotify artist profile
- Their related artists (as determined by Spotify's API)

---

## Purpose
Spotify provides rich data for artists, but its official dashboard can be limited. This custom dashboard gives you:

- Full control over the data display
- Strategic insights into your artist profile
- Comparison with related artists
- A base for expanding to even more analytics (like trends, history, charts)

---

## Architecture Diagram

```
Spotify User → Login with Spotify (OAuth2) → Flask App → Spotify API → Dashboard UI
```

---

## Features

### Authentication
- Secure Login via Spotify OAuth 2.0
- Required Spotify API Scopes:
  - `user-read-private`
  - `user-read-email`
  - `user-top-read`
  - `user-follow-read`

---

### Artist Metrics Dashboard (`/artist`)
Displays data about the authenticated Spotify artist:

- Artist Name
- Number of Followers
- Popularity Score (0-100)
- Genres
- Profile Image
- Spotify Profile Link

---

### Related Artists Dashboard (`/related-artists`)
Displays metrics about related artists:

- Artist Name
- Followers
- Popularity Score
- Profile Image
- Spotify Profile Link

---

## Routes Overview

| Route               | Description                                          |
|--------------------|------------------------------------------------------|
| `/`                | Homepage with Spotify Login                         |
| `/callback`        | OAuth2 callback from Spotify                        |
| `/artist`          | View your Spotify Artist Metrics                    |
| `/related-artists` | View Metrics for Related Artists                    |
| `/logout`          | Logout & Clear Session                              |

---

## Tech Stack

| Technology      | Purpose                                         |
|-----------------|-------------------------------------------------|
| Flask           | Backend Web Framework                          |
| Spotipy         | Python Spotify API Wrapper                     |
| Flask-Session   | User Session Management                        |
| python-dotenv   | Environment Variable Handling                  |
| TailwindCSS     | Modern, Responsive Design                      |
| Jinja2          | Dynamic HTML Templates                         |

---

## Project Structure

```
/spotify-app/
|-- run.py             # Main entry point to run the app
|-- .env               # Environment variables (KEEP THIS SECRET)
|-- requirements.txt   # Python dependencies
|-- .spotifycache      # Spotify token cache (auto-generated)
|-- .flask_session/    # Flask session files (auto-generated)
|-- app/               # Main application package
|   |-- __init__.py      # Application factory
|   |-- config.py        # Configuration classes
|   |-- main/            # Blueprint for core routes (home, auth)
|   |   |-- __init__.py
|   |   `-- routes.py
|   |-- spotify/         # Module for Spotify logic
|   |   |-- __init__.py
|   |   |-- auth.py        # Spotify OAuth and client helpers
|   |   |-- data.py        # Spotify data fetching functions
|   |   `-- utils.py       # Spotify related utilities (stats, parsing)
|   |-- playlists/       # Blueprint for playlist finding/outreach
|   |   |-- __init__.py
|   |   |-- routes.py      # Playlist finder and email routes
|   |   |-- playlistsupply.py # PlaylistSupply scraping logic
|   |   `-- email.py       # Gemini generation and SMTP sending
|   |-- static/          # Static files (CSS, JS if any)
|   |   `-- styles.css     # Your existing CSS (or empty)
|   `-- templates/       # Jinja2 templates
|       |-- base.html
|       |-- home.html
|       |-- artist.html
|       |-- search.html
|       |-- similar_genre.html
|       |-- playlist_finder_base.html
|       |-- playlist_finder_results.html
|       `-- _artist_display.html # Partial template
```

---

## Setup Instructions

1. Clone or download this repository.
2. Install dependencies:
```bash
pip install -r requirements.txt
```
3. Create your `.env` file:
```
SPOTIPY_CLIENT_ID=your_client_id
SPOTIPY_CLIENT_SECRET=your_client_secret
SPOTIPY_REDIRECT_URI=http://127.0.0.1:5000/callback
```
4. Run the app:
```bash
python main.py
```
5. Open in browser:
```
http://127.0.0.1:5000/
```

---

## Future Improvements (Optional Ideas)

- Store historical data for trend tracking
- Add charts & graphs for metrics
- Compare stats with other artists
- Export reports as PDF/CSV
- Notification system
- Admin dashboard
- Deployment to the cloud (Heroku / Render / AWS)

---

## Final Result

A clean, powerful, and customizable dashboard that provides instant visibility into the most important Spotify metrics for your artist profile and competitive landscape.

---

## License
MIT License

---


# Spotify Artist Metrics Dashboard 🎵

## Overview
This project is a Flask web application that allows a Spotify artist to log in with their Spotify account and visualize valuable metrics about:

- Their own Spotify artist profile
- Their related artists (as determined by Spotify's API)

---

## Purpose
Spotify provides rich data for artists, but its official dashboard can be limited. This custom dashboard gives you:

- Full control over the data display
- Strategic insights into your artist profile
- Comparison with related artists
- A base for expanding to even more analytics (like trends, history, charts)

---

## Architecture Diagram

```
Spotify User → Login with Spotify (OAuth2) → Flask App → Spotify API → Dashboard UI
```

---

## Features

### Authentication
- Secure Login via Spotify OAuth 2.0
- Required Spotify API Scopes:
  - `user-read-private`
  - `user-read-email`
  - `user-top-read`
  - `user-follow-read`

---

### Artist Metrics Dashboard (`/artist`)
Displays data about the authenticated Spotify artist:

- Artist Name
- Number of Followers
- Popularity Score (0-100)
- Genres
- Profile Image
- Spotify Profile Link

---

### Related Artists Dashboard (`/related-artists`)
Displays metrics about related artists:

- Artist Name
- Followers
- Popularity Score
- Profile Image
- Spotify Profile Link

---

## Routes Overview

| Route               | Description                                          |
|--------------------|------------------------------------------------------|
| `/`                | Homepage with Spotify Login                         |
| `/callback`        | OAuth2 callback from Spotify                        |
| `/artist`          | View your Spotify Artist Metrics                    |
| `/related-artists` | View Metrics for Related Artists                    |
| `/logout`          | Logout & Clear Session                              |

---

## Tech Stack

| Technology      | Purpose                                         |
|-----------------|-------------------------------------------------|
| Flask           | Backend Web Framework                          |
| Spotipy         | Python Spotify API Wrapper                     |
| Flask-Session   | User Session Management                        |
| python-dotenv   | Environment Variable Handling                  |
| TailwindCSS     | Modern, Responsive Design                      |
| Jinja2          | Dynamic HTML Templates                         |

---

## Project Structure

```
spotify_artist_dashboard/
├── main.py                --> Flask App Logic
├── requirements.txt       --> Python Dependencies
├── .env.example           --> Environment Variables Example
├── templates/             --> HTML Pages
│   ├── base.html
│   ├── home.html
│   ├── artist.html
│   └── related.html
└── static/
    └── styles.css         --> Tailwind Styles (Optional Overrides)
```

---

## Setup Instructions

1. Clone or download this repository.
2. Install dependencies:
```bash
pip install -r requirements.txt
```
3. Create your `.env` file:
```
SPOTIPY_CLIENT_ID=your_client_id
SPOTIPY_CLIENT_SECRET=your_client_secret
SPOTIPY_REDIRECT_URI=http://127.0.0.1:5000/callback
```
4. Run the app:
```bash
python main.py
```
5. Open in browser:
```
http://127.0.0.1:5000/
```

---

## Future Improvements (Optional Ideas)

- Store historical data for trend tracking
- Add charts & graphs for metrics
- Compare stats with other artists
- Export reports as PDF/CSV
- Notification system
- Admin dashboard
- Deployment to the cloud (Heroku / Render / AWS)

---

## Final Result

A clean, powerful, and customizable dashboard that provides instant visibility into the most important Spotify metrics for your artist profile and competitive landscape.

---

## License
MIT License

---
