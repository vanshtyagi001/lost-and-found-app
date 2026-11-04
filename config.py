import os
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

# Base directory for absolute paths
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Gemini API Keys (Reads from environment or .env, fallback to placeholder)
GEMINI_TEXT_API_KEY = os.environ.get("GEMINI_TEXT_API_KEY") or os.environ.get("GEMINI_API_KEY") or "YOUR API KEY"
GEMINI_IMAGE_API_KEY = os.environ.get("GEMINI_IMAGE_API_KEY") or os.environ.get("GEMINI_API_KEY") or "YOUR API KEY"

# --- App Configuration ---
SECRET_KEY = os.environ.get("FLASK_SECRET_KEY") or os.urandom(24)
DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///items.db")
# Render/Heroku compatibility fix for SQLAlchemy postgresql dialect
if DATABASE_URI.startswith("postgres://"):
    DATABASE_URI = DATABASE_URI.replace("postgres://", "postgresql://", 1)

# Absolute path for uploads directory
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or os.path.join(BASE_DIR, "uploads")
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# --- Admin Authentication Credentials ---
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

# --- Matching Thresholds (Configurable via .env with fallback defaults) ---
DESCRIPTION_SIMILARITY_THRESHOLD = float(os.environ.get("DESCRIPTION_SIMILARITY_THRESHOLD", 0.40))
METADATA_SIMILARITY_THRESHOLD = float(os.environ.get("METADATA_SIMILARITY_THRESHOLD", 0.50))
IMAGE_SIMILARITY_THRESHOLD = float(os.environ.get("IMAGE_SIMILARITY_THRESHOLD", 0.80))

# --- Gemini Model Configuration ---
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME", "gemini-3.1-flash-lite")
GEMINI_TEMPERATURE = float(os.environ.get("GEMINI_TEMPERATURE", 0.2))
