from flask import Blueprint

# Note: No template_folder specified, Flask looks in app/templates/ by default
main_bp = Blueprint('main', __name__)

# Import routes after blueprint creation to avoid circular imports
from . import routes