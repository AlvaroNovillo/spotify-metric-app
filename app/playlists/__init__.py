from flask import Blueprint

# No url_prefix, routes will be registered at the top level (e.g., /playlist-finder)
# No template_folder specified, uses app/templates/
playlists_bp = Blueprint('playlists', __name__)

# Import routes after blueprint creation
from . import routes