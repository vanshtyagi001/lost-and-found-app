# AI-Powered Lost and Found Platform

[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Flask Version](https://img.shields.io/badge/flask-3.0.x-orange.svg)](https://flask.palletsprojects.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT) <!-- Choose appropriate license -->

A web-based platform that helps reunite lost items with their owners using AI image analysis powered by Google's Gemini API.

## Core Features

*   **Report Found Items:** Users who find an item can upload a photo. The Gemini API automatically generates a description. Users add details like item type, color, brand, location found, and contact information.
*   **Search Lost Items:** Users who lost an item can upload a photo (of the item or a similar one). Gemini generates a description. Users provide details like item type, color, brand, and last known location.
*   **Intelligent Matching:** A three-tiered algorithm compares lost and found items:
    1.  **Description Matching:** Semantic similarity between AI-generated descriptions (using Gemini).
    2.  **Metadata Matching:** Compares item type, color, brand, and basic location proximity.
    3.  **Image Comparison:** Visual similarity analysis between images (using Gemini).
*   **Match Results:** Displays potential matches with details, images, finder contact info, and a confidence score.

## Technologies Used

*   **Backend:** Python, Flask
*   **Database:** SQLite (for development/simplicity)
*   **AI:** Google Gemini API (`gemini-1.5-flash-latest` model via `google-generativeai` SDK)
    *   Image Description Generation
    *   Semantic Text Similarity
    *   Visual Image Similarity
*   **Frontend:** HTML, Jinja2 Templates, CSS (Bootstrap optionally used for basic styling)
*   **Image Processing:** Pillow (PIL Fork)

## Project Structure
lost-and-found-app/
├── app.py # Main Flask application logic, routes, API calls
├── models.py # SQLAlchemy database models (Item table)
├── config.py # Configuration (API keys, DB URI, thresholds) - IMPORTANT: Keep keys secure!
├── requirements.txt # Python dependencies
├── static/ # Static files (CSS, JS, etc.)
│ └── css/
│ └── style.css
├── templates/ # HTML templates (Jinja2)
│ ├── base.html
│ ├── index.html
│ ├── report_found.html
│ ├── search_lost.html
│ └── results.html
├── uploads/ # Directory for storing uploaded images (gitignore recommended)
├── .env.example # Example for environment variables (Recommended for API keys)
├── .gitignore # Specifies intentionally untracked files git should ignore
└── README.md # This file
## Setup and Installation

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/YOUR_USERNAME/lost-and-found-app.git
    cd lost-and-found-app
    ```

2.  **Create a Virtual Environment:** (Recommended)
    ```bash
    python -m venv .venv
    # On Windows
    .\.venv\Scripts\activate
    # On macOS/Linux
    source .venv/bin/activate
    ```

3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Configure API Keys:**
    *   **Option A (Recommended): Environment Variables**
        *   Create a file named `.env` in the project root.
        *   Copy the contents of `.env.example` into `.env`.
        *   Replace the placeholder values in `.env` with your actual Google AI Studio API keys:
            ```dotenv
            # .env file
            GEMINI_TEXT_API_KEY=AIzaSyAGgQ12y7ytWNUqjWdjnm6Va1iJFpb5f4c_YOUR_REAL_KEY
            GEMINI_IMAGE_API_KEY=AIzaSyDyfUFysP_nUUmUPFHwq9APuzLsT04qeVY_YOUR_REAL_KEY
            FLASK_SECRET_KEY=your_strong_random_secret_key_here # Generate a strong secret key
            ```
        *   Modify `config.py` to load keys from environment variables (see comments within `config.py` for example using `os.environ.get()`).
    *   **Option B (Simpler, Less Secure): Edit `config.py` directly**
        *   Open `config.py`.
        *   Replace the placeholder strings for `GEMINI_TEXT_API_KEY` and `GEMINI_IMAGE_API_KEY` with your actual keys.
        *   **WARNING:** Do not commit your actual API keys directly into public repositories if using this method. Add `config.py` to your `.gitignore` file if you put real keys in it.
        *   Also set a strong `SECRET_KEY` in `config.py`.

5.  **(Optional) Configure Matching Thresholds:** Adjust the threshold values in `config.py` if needed:
    ```python
    DESCRIPTION_SIMILARITY_THRESHOLD = 0.40
    METADATA_SIMILARITY_THRESHOLD = 0.50
    IMAGE_SIMILARITY_THRESHOLD = 0.80
    ```

6.  **Run the Application:**
    ```bash
    flask run
    # or
    python app.py
    ```

7.  **Access the Platform:** Open your web browser and navigate to `http://127.0.0.1:5000` (or the address provided by Flask).

## Important Considerations

*   **API Key Security:** **Never** commit your API keys directly to Git. Use environment variables (`.env` file) and ensure `.env` is listed in your `.gitignore` file.
*   **Database:** SQLite is used for simplicity. For production or larger scale, consider migrating to PostgreSQL or MySQL. Use `Flask-Migrate` for managing database schema changes.
*   **Error Handling:** Production applications need more robust error handling and logging.
*   **Security:** Implement proper input validation/sanitization (especially for contact info), protect against common web vulnerabilities (XSS, CSRF), and consider user authentication/authorization.
*   **Contact Info Privacy:** The current implementation directly displays finder contact info. In a real-world app, implement masking or an internal messaging system to protect user privacy.
*   **Location Matching:** The current location matching is based on simple text comparison. For accurate proximity matching, integrate geospatial libraries (like Geopy) and calculate distances.
*   **Performance:** For large numbers of items, matching can be slow due to API calls. Consider optimizations like database pre-filtering, asynchronous processing (asyncio/Celery), or pre-computing image embeddings for faster similarity search.
*   **Deployment:** Use a production-ready WSGI server (like Gunicorn or Waitress) instead of the Flask development server for deployment.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request or open an Issue.

Before Committing:
Create .gitignore: Make sure you have a .gitignore file in your project root. Add entries like:
# Python
*.pyc
__pycache__/
.venv/
instance/
*.sqlite
*.db
*.db-journal # SQLite temporary files

# Config / Secrets
.env
# config.py # Add this ONLY if you put real secrets directly in config.py

# Uploads (Usually don't commit user uploads)
uploads/

# IDE / OS files
.idea/
.vscode/
*.DS_Store

Create .env.example: Create this file with placeholder keys as shown in the README.
Add a License File: If you choose a license like MIT, create a LICENSE.md file and paste the standard MIT license text into it.
Replace Placeholders: Change YOUR_USERNAME in the git clone command to your actual GitHub username.
Review: Read through the generated README and adjust any details specific to your final implementation.
