import os
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

# Gemini API Keys (Reads from environment or .env, fallback to placeholder)
GEMINI_TEXT_API_KEY = os.environ.get("GEMINI_TEXT_API_KEY") or os.environ.get("GEMINI_API_KEY") or "YOUR API KEY"
GEMINI_IMAGE_API_KEY = os.environ.get("GEMINI_IMAGE_API_KEY") or os.environ.get("GEMINI_API_KEY") or "YOUR API KEY"

# --- App Configuration ---
SECRET_KEY = os.environ.get("FLASK_SECRET_KEY") or os.urandom(24)
DATABASE_URI = 'sqlite:///items.db'
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# --- Matching Thresholds ---
DESCRIPTION_SIMILARITY_THRESHOLD = 0.40
METADATA_SIMILARITY_THRESHOLD = 0.50
IMAGE_SIMILARITY_THRESHOLD = 0.80

# --- Gemini Model ---
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME", "gemini-3.1-flash-lite")
