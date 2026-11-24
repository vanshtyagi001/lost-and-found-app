import os
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, abort, session, jsonify
from functools import wraps
import re
from datetime import datetime

# Import config and models
try:
    from config import (
        SECRET_KEY, DATABASE_URI,
        UPLOAD_FOLDER, ALLOWED_EXTENSIONS,
        ADMIN_USERNAME, ADMIN_PASSWORD
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

from services import (
    ITEM_TYPES,
    allowed_file,
    clean_ai_description,
    find_matching_found_items,
    generate_description_gemini,
    save_uploaded_image,
)
from api_v1 import api_v1

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
    # Ensure database tables, instance path, and upload directory exist on startup (supports Gunicorn & local dev)
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    with app.app_context():
        db.create_all()
        print("Database tables initialized successfully.")
    print(f"Upload directory verified: {app.config['UPLOAD_FOLDER']}")
except Exception as e:
    print(f"CRITICAL ERROR during app initialization: {e}")
    exit()

# Register JSON REST API v1 Blueprint for native mobile clients
app.register_blueprint(api_v1)

# --- Markdown Parser & Template Filter ---
from markupsafe import Markup
try:
    from markdown_it import MarkdownIt
    md_parser = MarkdownIt("commonmark", {"breaks": True, "html": False})
except Exception:
    md_parser = None

@app.template_filter('render_markdown')
def render_markdown_filter(text):
    """Converts markdown formatting (e.g. **bold**, lists) to safe HTML after stripping conversational preambles."""
    if not text:
        return ""
    cleaned = clean_ai_description(str(text))
    if md_parser:
        return Markup(md_parser.render(cleaned))
    # Fallback formatting
    content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', cleaned)
    content = content.replace('\n', '<br>')
    return Markup(content)

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
    item_types = ITEM_TYPES
    if request.method == 'POST':
        if 'item_image' not in request.files: flash('No image file part selected', 'danger'); return redirect(request.url)
        file = request.files['item_image']
        if file.filename == '': flash('No image selected for uploading', 'danger'); return redirect(request.url)
        item_type = request.form.get('item_type'); location = request.form.get('location'); contact_info = request.form.get('contact_info')
        if not all([item_type, location, contact_info]): flash('Item Type, Location Found, and Contact Info are required fields.', 'danger'); return render_template('report_found.html', item_types=item_types, entered_data=request.form)
        if not file or not allowed_file(file.filename, app.config['ALLOWED_EXTENSIONS']): flash('Invalid file type. Allowed types: png, jpg, jpeg, gif', 'danger'); return render_template('report_found.html', item_types=item_types, entered_data=request.form)

        filepath = None
        try:
            filename, filepath = save_uploaded_image(file, app.config['UPLOAD_FOLDER'], prefix="found")
            print(f"Image saved to: {filepath}")

            ai_description = generate_description_gemini(filepath)
            if ai_description.startswith("Error:"): flash(f'AI description failed: {ai_description}. Item saved.', 'warning'); ai_description = "AI description generation failed."

            color = request.form.get('color')
            brand = request.form.get('brand')
            new_item = Item(
                status='found',
                item_type=item_type,
                color=color,
                brand=brand,
                location=location,
                image_filename=filename,
                ai_description=ai_description,
                contact_info=contact_info,
            )
            db.session.add(new_item)
            db.session.commit()
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
    item_types = ITEM_TYPES
    if request.method == 'POST':
        if 'item_image' not in request.files: flash('No image file part selected for search', 'danger'); return redirect(request.url)
        file = request.files['item_image']
        if file.filename == '': flash('No image selected for searching', 'danger'); return redirect(request.url)
        item_type = request.form.get('item_type'); location = request.form.get('location')
        if not all([item_type, location]): flash('Item Type and Last Known Location are required.', 'danger'); return render_template('search_lost.html', item_types=item_types, entered_data=request.form)
        if not file or not allowed_file(file.filename, app.config['ALLOWED_EXTENSIONS']): flash('Invalid file type for search image.', 'danger'); return render_template('search_lost.html', item_types=item_types, entered_data=request.form)

        search_filepath = None
        try:
            search_filename, search_filepath = save_uploaded_image(file, app.config['UPLOAD_FOLDER'], prefix="search")
            print(f"Search image saved temporarily to: {search_filepath}")

            lost_ai_description = generate_description_gemini(search_filepath)
            if lost_ai_description.startswith("Error:"): flash(f'AI description failed: {lost_ai_description}. Search quality might be affected.', 'warning'); lost_ai_description = ""

            lost_item_details = {"item_type": item_type.lower(), "color": request.form.get('color', '').lower(), "brand": request.form.get('brand', '').lower(), "location": location.lower(), "ai_description": lost_ai_description, "image_path": search_filepath}
            print(f"\n--- Starting Match Process for {search_filename} ---")
            matches = find_matching_found_items(lost_item_details, app.config['UPLOAD_FOLDER'])

            if search_filepath and os.path.exists(search_filepath):
                try: os.remove(search_filepath); print(f"Temporary search image removed: {search_filepath}")
                except OSError as e: print(f"Error removing temporary search image {search_filepath}: {e}")

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

# --- Admin Authentication & Routes ---

def admin_required(f):
    """Decorator that protects admin endpoints with session-based authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            next_url = request.full_path if request.method == 'GET' and not request.full_path.startswith('/admin/login') else None
            return redirect(url_for('admin_login', next=next_url))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Handles admin sign-in with credentials."""
    if session.get('admin_logged_in'):
        return redirect(url_for('admin'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            flash('Welcome back, Administrator.', 'success')
            next_url = request.args.get('next')
            if next_url and next_url.startswith('/') and not next_url.startswith('//'):
                return redirect(next_url)
            return redirect(url_for('admin'))
        else:
            flash('Invalid username or password. Please try again.', 'danger')
            return render_template('admin_login.html', entered_username=username)

    return render_template('admin_login.html', entered_username='')

@app.route('/admin/logout')
def admin_logout():
    """Logs out the administrator and clears session."""
    session.pop('admin_logged_in', None)
    flash('You have been logged out of the administrator panel.', 'info')
    return redirect(url_for('admin_login'))

@app.route('/admin')
@admin_required
def admin():
    """Renders the developer admin dashboard listing all items."""
    items = Item.query.order_by(Item.timestamp.desc()).all()
    return render_template('admin.html', items=items)

@app.route('/admin/delete/<int:item_id>', methods=['POST'])
@admin_required
def admin_delete_item(item_id):
    """Deletes an item from the database and removes its uploaded image."""
    item = Item.query.get_or_404(item_id)
    try:
        if item.image_filename:
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], item.image_filename)
            if os.path.exists(image_path) and os.path.isfile(image_path):
                os.remove(image_path)
                print(f"Admin deleted image file: {image_path}")

        db.session.delete(item)
        db.session.commit()
        flash(f'Item #{item_id} ({item.item_type}) deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting item #{item_id}: {e}', 'danger')
        print(f"Error deleting item #{item_id}: {e}")

    return redirect(url_for('admin'))

@app.route('/admin/clear_all', methods=['POST'])
@admin_required
def admin_clear_all():
    """Clears all items from the database and removes their uploaded images."""
    try:
        items = Item.query.all()
        deleted_files_count = 0
        for item in items:
            if item.image_filename:
                image_path = os.path.join(app.config['UPLOAD_FOLDER'], item.image_filename)
                if os.path.exists(image_path) and os.path.isfile(image_path):
                    try:
                        os.remove(image_path)
                        deleted_files_count += 1
                    except OSError as err:
                        print(f"Could not delete image {image_path}: {err}")

        num_deleted = Item.query.delete()
        db.session.commit()
        flash(f'Successfully cleared {num_deleted} items and {deleted_files_count} images.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error clearing database: {e}', 'danger')
        print(f"Error clearing all items: {e}")

    return redirect(url_for('admin'))

# --- Database Initialization ---
def init_db():
     with app.app_context():
        print("Attempting to create database tables if they don't exist...")
        try:
            db.create_all()
            print("Database tables checked/created successfully.")
        except Exception as e:
            print(f"CRITICAL Error creating database tables: {e}")

# --- Error Handlers ---
@app.errorhandler(413)
def handle_request_entity_too_large(e):
    """Returns JSON for API requests exceeding MAX_CONTENT_LENGTH."""
    if request.path.startswith("/api/"):
        return jsonify({"error": "Uploaded file exceeds the 16MB size limit."}), 413
    flash("Uploaded file is too large (max 16MB).", "danger")
    return redirect(url_for('index'))

@app.errorhandler(500)
def handle_internal_server_error(e):
    """Logs traceback for production debugging and renders clean message."""
    import traceback
    print("=== FLASK 500 INTERNAL SERVER ERROR ===")
    traceback.print_exc()
    print("========================================")
    try:
        db.session.rollback()
    except Exception:
        pass
    if request.path.startswith("/api/"):
        return jsonify({"error": "Internal server error."}), 500
    flash("An unexpected server error occurred while processing your request. Please try again.", "danger")
    return render_template('index.html'), 500

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