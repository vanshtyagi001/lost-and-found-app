import os
import google.generativeai as genai
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, abort
from werkzeug.utils import secure_filename
from PIL import Image as PILImage # Use PILImage to avoid conflict
import io
import time
import re # Import regex for parsing Gemini responses
from datetime import datetime

# Import config and models
try:
    from config import (
        GEMINI_TEXT_API_KEY, GEMINI_IMAGE_API_KEY, SECRET_KEY, DATABASE_URI,
        UPLOAD_FOLDER, ALLOWED_EXTENSIONS, GEMINI_MODEL_NAME,
        DESCRIPTION_SIMILARITY_THRESHOLD, METADATA_SIMILARITY_THRESHOLD,
        IMAGE_SIMILARITY_THRESHOLD
    )
except ImportError as e:
    print(f"CRITICAL ERROR: Could not import from config.py: {e}")
    print("Ensure config.py exists and has correct variables.")
    exit()

try:
    from models import db, Item
except ImportError as e:
    print(f"CRITICAL ERROR: Could not import from models.py: {e}")
    print("Ensure models.py exists and defines db and Item.")
    exit()

# --- Flask App Setup ---
app = Flask(__name__)
try:
    app.config['SECRET_KEY'] = SECRET_KEY
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URI
    app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 # 16MB max upload size
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    # Store extensions in config for allowed_file helper
    app.config['ALLOWED_EXTENSIONS'] = ALLOWED_EXTENSIONS
    print("Flask app configured.")
except Exception as e:
    print(f"CRITICAL ERROR: Failed to set Flask config values: {e}")
    exit()

# Initialize SQLAlchemy
try:
    db.init_app(app)
    print("SQLAlchemy initialized with app.")
except Exception as e:
    print(f"CRITICAL ERROR: db.init_app(app) failed: {e}")
    exit()

# --- Gemini API Configuration ---
print("--- Attempting Gemini API Configuration ---")
try:
    genai.configure(api_key=GEMINI_TEXT_API_KEY)
    print(f"Gemini API configured successfully (using text key ending ...{GEMINI_TEXT_API_KEY[-4:]}).")
except Exception as e:
    print(f"ERROR during genai.configure: {e}. Check GEMINI_TEXT_API_KEY.")

text_model = None
vision_model = None
try:
    text_model = genai.GenerativeModel(GEMINI_MODEL_NAME)
    vision_model = genai.GenerativeModel(GEMINI_MODEL_NAME) # Assumes same model okay
    print(f"Gemini models ('{GEMINI_MODEL_NAME}') initialized successfully.")
except Exception as e:
     print(f"ERROR creating Gemini models ('{GEMINI_MODEL_NAME}'): {e}. AI features will likely fail.")
print("--- Finished Gemini API Configuration Attempt ---")

# --- Helper Functions ---

def allowed_file(filename):
    """Checks if the file extension is allowed."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def generate_description_gemini(image_path):
    """Generates a description for an image using Gemini."""
    if not text_model:
        print("Attempted to generate description, but text_model is not available.")
        return "Error: Gemini text model not initialized."
    if not os.path.exists(image_path):
        print(f"Image file not found at path: {image_path}")
        return "Error: Image file not found."

    try:
        print(f"Generating description for: {os.path.basename(image_path)}")
        img = PILImage.open(image_path)
        if img.mode != 'RGB': img = img.convert('RGB')
        prompt = "Describe this item in detail for a lost and found platform. Focus on visual characteristics like type, color, material, shape, and any unique markings."
        contents = [prompt, img]
        response = text_model.generate_content(contents)
        print(f"Gemini Description Response: {response.text[:100]}...")
        return response.text.strip() if hasattr(response, 'text') and response.text else "AI could not generate a description."
    except Exception as e:
        print(f"Error generating description with Gemini: {e}")
        error_message = f"Error generating AI description: {type(e).__name__}."
        if "API key" in str(e): error_message = "Error: Invalid/missing Gemini API key (Text)."
        elif "quota" in str(e).lower(): error_message = "Error: Gemini API quota exceeded."
        elif "DeadlineExceeded" in str(e): error_message = "Error: Gemini API request timed out."
        elif "ResourceExhausted" in str(e): error_message = "Error: Gemini API resource exhausted."
        return error_message

def compare_descriptions_gemini(desc1, desc2):
    """Compares two descriptions for semantic similarity using Gemini."""
    if not text_model:
        print("Attempted description comparison, but text_model is not available.")
        return 0.0
    if not desc1 or not desc2: return 0.0

    try:
        print("Comparing descriptions...")
        prompt = f"""
        On a scale of 0.0 to 1.0, how semantically similar are these descriptions?
        1: "{desc1}"
        2: "{desc2}"
        Respond ONLY with the numerical score (e.g., 0.75).
        """
        response = text_model.generate_content(prompt)
        print(f"Gemini Description Similarity Response: {response.text}")
        try:
            match = re.search(r"[-+]?\d*\.\d+|\d+", response.text)
            if match:
                similarity = float(match.group())
                return max(0.0, min(1.0, similarity))
            else:
                 print(f"Warning: Could not parse number from description similarity response: {response.text}")
                 return 0.0
        except (ValueError, AttributeError) as parse_err:
            print(f"Warning: Error parsing similarity score ({parse_err}): {response.text}")
            return 0.0
    except Exception as e:
        print(f"Error comparing descriptions with Gemini: {e}")
        return 0.0

def compare_images_gemini(image_path1, image_path2):
    """Compares two images for visual similarity using Gemini Vision."""
    if not vision_model:
        print("Attempted image comparison, but vision_model is not available.")
        return 0.0
    if not os.path.exists(image_path1): print(f"Image file 1 not found: {image_path1}"); return 0.0
    if not os.path.exists(image_path2): print(f"Image file 2 not found: {image_path2}"); return 0.0

    try:
        print(f"Comparing images: {os.path.basename(image_path1)} and {os.path.basename(image_path2)}")
        img1 = PILImage.open(image_path1); img2 = PILImage.open(image_path2)
        if img1.mode != 'RGB': img1 = img1.convert('RGB')
        if img2.mode != 'RGB': img2 = img2.convert('RGB')
        prompt = """
        On a scale of 0.0 to 1.0, how visually similar are the items in these two images?
        Respond ONLY with the numerical score (e.g., 0.90).
        """
        contents = [prompt, img1, img2]
        response = vision_model.generate_content(contents)
        print(f"Gemini Image Similarity Response: {response.text}")
        try:
            match = re.search(r"[-+]?\d*\.\d+|\d+", response.text)
            if match:
                similarity = float(match.group())
                return max(0.0, min(1.0, similarity))
            else:
                 print(f"Warning: Could not parse number from image similarity response: {response.text}")
                 return 0.0
        except (ValueError, AttributeError) as parse_err:
            print(f"Warning: Error parsing image similarity score ({parse_err}): {response.text}")
            return 0.0
    except Exception as e:
        print(f"Error comparing images with Gemini: {e}")
        return 0.0

def calculate_metadata_similarity(item_meta1, item_meta2):
    """Calculates similarity based on item type, color, brand, location."""
    score = 0; max_possible_score = 0
    # Item Type (Exact Match, High Weight)
    max_possible_score += 1.5
    if item_meta1.get("item_type") and item_meta1["item_type"] == item_meta2.get("item_type"): score += 1.5
    # Color (Partial Match)
    max_possible_score += 1.0
    if item_meta1.get("color") and item_meta1["color"] == item_meta2.get("color"): score += 1.0
    elif item_meta1.get("color") and item_meta2.get("color") and (item_meta1["color"] in item_meta2["color"] or item_meta2["color"] in item_meta1["color"]): score += 0.5
    # Brand (Partial Match, Bonus if both missing)
    max_possible_score += 1.0
    if item_meta1.get("brand") and item_meta1["brand"] == item_meta2.get("brand"): score += 1.0
    elif item_meta1.get("brand") and item_meta2.get("brand") and (item_meta1["brand"] in item_meta2["brand"] or item_meta2["brand"] in item_meta1["brand"]): score += 0.5
    elif not item_meta1.get("brand") and not item_meta2.get("brand"): score += 0.25
    # Location (Simple Substring Match)
    max_possible_score += 1.0
    if item_meta1.get("location") and item_meta1["location"] == item_meta2.get("location"): score += 1.0
    elif item_meta1.get("location") and item_meta2.get("location") and (item_meta1["location"] in item_meta2["location"] or item_meta2["location"] in item_meta1["location"]): score += 0.5
    return score / max_possible_score if max_possible_score > 0 else 0

# --- Context Processor ---
@app.context_processor
def inject_now():
    """Injects the current datetime into the template context."""
    return {'now': datetime.utcnow}

# --- Routes ---

@app.route('/')
def index():
    """Renders the home page."""
    print("Serving index page.")
    return render_template('index.html')

@app.route('/report_found', methods=['GET', 'POST'])
def report_found():
    """Handles reporting a found item."""
    item_types = ["Electronics", "Keys", "Wallet/Purse", "Clothing", "Bag/Backpack", "Jewelry/Watch", "Book/Notebook", "Pet", "Identification", "Other"]
    if request.method == 'POST':
        if 'item_image' not in request.files: flash('No image file part selected', 'danger'); return redirect(request.url)
        file = request.files['item_image']
        if file.filename == '': flash('No image selected for uploading', 'danger'); return redirect(request.url)
        item_type = request.form.get('item_type'); location = request.form.get('location'); contact_info = request.form.get('contact_info')
        if not all([item_type, location, contact_info]): flash('Item Type, Location Found, and Contact Info are required fields.', 'danger'); return render_template('report_found.html', item_types=item_types, entered_data=request.form)
        if not file or not allowed_file(file.filename): flash('Invalid file type. Allowed types: png, jpg, jpeg, gif', 'danger'); return render_template('report_found.html', item_types=item_types, entered_data=request.form)

        filepath = None
        try:
            basefilename, file_extension = os.path.splitext(file.filename)
            safe_base = secure_filename(basefilename)
            filename = f"found_{int(time.time())}_{safe_base}{file_extension}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            print(f"Image saved to: {filepath}")

            ai_description = generate_description_gemini(filepath)
            if ai_description.startswith("Error:"): flash(f'AI description failed: {ai_description}. Item saved.', 'warning'); ai_description = "AI description generation failed."

            color = request.form.get('color'); brand = request.form.get('brand')
            new_item = Item(status='found', item_type=item_type, color=color, brand=brand, location=location, image_filename=filename, ai_description=ai_description, contact_info=contact_info)
            db.session.add(new_item); db.session.commit()
            flash('Found item reported successfully!', 'success')
            if not ai_description.startswith("AI description generation failed"): flash(f'AI Generated Description: {ai_description}', 'info')
            return redirect(url_for('index'))
        except Exception as e:
            db.session.rollback(); flash(f'An error occurred: {e}', 'danger'); print(f"Error reporting found item: {e}")
            if filepath and os.path.exists(filepath):
                 try: os.remove(filepath); print(f"Cleaned up failed upload: {filepath}")
                 except OSError as rm_err: print(f"Error removing file after failure: {rm_err}")
            return render_template('report_found.html', item_types=item_types, entered_data=request.form) # Return to form

    # GET request
    return render_template('report_found.html', item_types=item_types, entered_data={})

@app.route('/search_lost', methods=['GET', 'POST'])
def search_lost():
    """Handles searching for a lost item."""
    item_types = ["Electronics", "Keys", "Wallet/Purse", "Clothing", "Bag/Backpack", "Jewelry/Watch", "Book/Notebook", "Pet", "Identification", "Other"]
    if request.method == 'POST':
        if 'item_image' not in request.files: flash('No image file part selected for search', 'danger'); return redirect(request.url)
        file = request.files['item_image']
        if file.filename == '': flash('No image selected for searching', 'danger'); return redirect(request.url)
        item_type = request.form.get('item_type'); location = request.form.get('location')
        if not all([item_type, location]): flash('Item Type and Last Known Location are required.', 'danger'); return render_template('search_lost.html', item_types=item_types, entered_data=request.form)
        if not file or not allowed_file(file.filename): flash('Invalid file type for search image.', 'danger'); return render_template('search_lost.html', item_types=item_types, entered_data=request.form)

        search_filepath = None
        try:
            basefilename, file_extension = os.path.splitext(file.filename)
            safe_base = secure_filename(basefilename)
            search_filename = f"search_{int(time.time())}_{safe_base}{file_extension}"
            search_filepath = os.path.join(app.config['UPLOAD_FOLDER'], search_filename)
            file.save(search_filepath)
            print(f"Search image saved temporarily to: {search_filepath}")

            lost_ai_description = generate_description_gemini(search_filepath)
            if lost_ai_description.startswith("Error:"): flash(f'AI description failed: {lost_ai_description}. Search quality might be affected.', 'warning'); lost_ai_description = ""

            lost_item_details = {"item_type": item_type.lower(), "color": request.form.get('color', '').lower(), "brand": request.form.get('brand', '').lower(), "location": location.lower(), "ai_description": lost_ai_description, "image_path": search_filepath}
            found_items = Item.query.filter_by(status='found').all()
            matches = []
            print(f"\n--- Starting Match Process for {search_filename} ---")

            for found_item in found_items:
                print(f"\nComparing with Found Item ID: {found_item.id} ({found_item.item_type})")
                match_scores = {"description": 0.0, "metadata": 0.0, "image": 0.0, "confidence": 0.0}
                desc_similarity = 0.0; meta_similarity = 0.0; img_similarity = 0.0

                # Tier 1: Description
                if lost_item_details["ai_description"] and found_item.ai_description and "failed" not in found_item.ai_description.lower():
                    desc_similarity = compare_descriptions_gemini(lost_item_details["ai_description"], found_item.ai_description)
                    match_scores["description"] = desc_similarity
                    print(f"  Desc Similarity: {desc_similarity:.2f} (Thresh: {DESCRIPTION_SIMILARITY_THRESHOLD})")
                else: print("  Skipping description comparison (missing/failed).")

                if desc_similarity < DESCRIPTION_SIMILARITY_THRESHOLD: print("  Skipping further checks (low desc similarity)."); continue

                # Tier 2: Metadata
                found_item_meta = found_item.get_metadata()
                meta_similarity = calculate_metadata_similarity(lost_item_details, found_item_meta)
                match_scores["metadata"] = meta_similarity
                print(f"  Meta Similarity: {meta_similarity:.2f} (Thresh: {METADATA_SIMILARITY_THRESHOLD})")

                if meta_similarity < METADATA_SIMILARITY_THRESHOLD: print("  Skipping further checks (low meta similarity)."); continue

                # Tier 3: Image
                found_image_path = os.path.join(app.config['UPLOAD_FOLDER'], found_item.image_filename)
                if os.path.exists(found_image_path):
                    img_similarity = compare_images_gemini(lost_item_details["image_path"], found_image_path)
                    match_scores["image"] = img_similarity
                    print(f"  Image Similarity: {img_similarity:.2f} (Thresh: {IMAGE_SIMILARITY_THRESHOLD})")
                else: print(f"  Skipping image comparison: Found item image not found at {found_image_path}")

                if img_similarity < IMAGE_SIMILARITY_THRESHOLD: print("  Match failed image similarity threshold."); continue

                # --- Match Found! ---
                confidence = (desc_similarity * 0.3) + (meta_similarity * 0.3) + (img_similarity * 0.4)
                match_scores["confidence"] = confidence
                print(f"  >>> STRONG MATCH FOUND! Confidence: {confidence:.2f}")
                matches.append({"found_item": found_item, "scores": match_scores, "confidence": confidence})

            if search_filepath and os.path.exists(search_filepath):
                try: os.remove(search_filepath); print(f"Temporary search image removed: {search_filepath}")
                except OSError as e: print(f"Error removing temporary search image {search_filepath}: {e}")

            matches.sort(key=lambda x: x["confidence"], reverse=True)
            return render_template('results.html', matches=matches, search_description=lost_ai_description if lost_ai_description else "N/A")

        except Exception as e:
            flash(f'An error occurred during search: {e}', 'danger'); print(f"Error during search process: {e}")
            if search_filepath and os.path.exists(search_filepath):
                try: os.remove(search_filepath)
                except OSError as rm_err: print(f"Error removing search file after failure: {rm_err}")
            return redirect(url_for('search_lost')) # Redirect back to search form

    # GET request
    return render_template('search_lost.html', item_types=item_types, entered_data={})

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    """Serves uploaded files from the UPLOAD_FOLDER securely."""
    if '..' in filename or filename.startswith('/'): abort(404)
    try:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=False)
    except FileNotFoundError: abort(404)

# --- Database Initialization ---
def init_db():
     with app.app_context():
        print("Attempting to create database tables if they don't exist...")
        try:
            db.create_all()
            print("Database tables checked/created successfully.")
        except Exception as e:
            print(f"CRITICAL Error creating database tables: {e}")

# --- Run Application ---
if __name__ == '__main__':
    print("--- Starting Lost & Found Application ---")
    # Ensure the upload folder exists
    if not os.path.exists(UPLOAD_FOLDER):
        try: os.makedirs(UPLOAD_FOLDER); print(f"Created upload directory: {UPLOAD_FOLDER}")
        except OSError as e: print(f"CRITICAL Error creating upload directory {UPLOAD_FOLDER}: {e}")

    # Initialize the database
    init_db()

    # Run the Flask development server
    print("Starting Flask development server...")
    # Use port 5000 again for the main app
    app.run(debug=True, host='0.0.0.0', port=5000)
    print("--- Lost & Found Application Stopped ---")