import os

# --- IMPORTANT ---
# Replace with your ACTUAL Google AI Studio API Keys
# It's highly recommended to use environment variables instead of hardcoding
# Example using os.environ.get():
# GEMINI_TEXT_API_KEY = os.environ.get("GEMINI_TEXT_KEY")
# GEMINI_IMAGE_API_KEY = os.environ.get("GEMINI_IMAGE_KEY")
# Ensure you set these environment variables in your system.

# Using the placeholder keys from the prompt for structure:
# Replace these with your real keys! DO NOT COMMIT REAL KEYS TO GIT!
GEMINI_TEXT_API_KEY = "YOUR API KEY" # For description generation & text similarity
GEMINI_IMAGE_API_KEY = "YOUR API KEY" # For image visual similarity

# --- App Configuration ---
SECRET_KEY = os.urandom(24) # Replace with a fixed, strong secret key for production
DATABASE_URI = 'sqlite:///items.db'
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# --- Matching Thresholds ---
DESCRIPTION_SIMILARITY_THRESHOLD = 0.40
METADATA_SIMILARITY_THRESHOLD = 0.50
IMAGE_SIMILARITY_THRESHOLD = 0.80

# --- Gemini Model ---
GEMINI_MODEL_NAME = 'gemini-1.5-flash-latest' # Use the current appropriate flash model
