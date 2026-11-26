import os
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, abort, session, jsonify
from functools import wraps
from werkzeug.security import check_password_hash, generate_password_hash
import re
from datetime import datetime

# Import config and models
try:
    from config import (
        SECRET_KEY, DATABASE_URI,
        UPLOAD_FOLDER, ALLOWED_EXTENSIONS,
        ADMIN_USERNAME, ADMIN_PASSWORD, GOOGLE_CLIENT_ID
    )
except ImportError as e:
    print(f"CRITICAL ERROR: Could not import from config.py: {e}")
    print("Ensure config.py exists and has correct variables.")
    exit()

try:
    from models import db, Item, User
except ImportError as e:
    print(f"CRITICAL ERROR: Could not import from models.py: {e}")
    print("Ensure models.py exists and defines db and Item.")
    exit()

from services import (
    ITEM_TYPES,
    allowed_file,
    clean_ai_description,
    delete_item_image,
    find_matching_found_items,
    generate_description_gemini,
    get_or_create_google_user,
    save_found_image,
    save_uploaded_image,
    verify_google_id_token,
)
from api_v1 import api_v1

# --- Flask App Setup ---
app = Flask(__name__)
try:
    app.config['SECRET_KEY'] = SECRET_KEY
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URI
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'pool_recycle': 280,
    }
    app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 # 16MB max upload size
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    # Store extensions in config for allowed_file helper
    app.config['ALLOWED_EXTENSIONS'] = ALLOWED_EXTENSIONS
    print("Flask app configured.")
except Exception as e:
    print(f"CRITICAL ERROR: Failed to set Flask config values: {e}")
    exit()

def _ensure_user_profile_columns():
    """Safely adds new schema columns if running against an existing database."""
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())
    if "user" in table_names:
        existing = {col["name"] for col in inspector.get_columns("user")}
        new_cols = {
            "avatar_url": "VARCHAR(500)",
            "phone": "VARCHAR(30)",
            "address": "VARCHAR(300)",
            "city": "VARCHAR(100)",
            "alternate_contact": "VARCHAR(150)",
        }
        for col_name, col_type in new_cols.items():
            if col_name not in existing:
                db.session.execute(text(f'ALTER TABLE "user" ADD COLUMN {col_name} {col_type}'))

    if "item" in table_names:
        existing_item_cols = {col["name"] for col in inspector.get_columns("item")}
        if "user_id" not in existing_item_cols:
            db.session.execute(text('ALTER TABLE item ADD COLUMN user_id INTEGER REFERENCES "user"(id)'))

    db.session.commit()


# Initialize SQLAlchemy
try:
    db.init_app(app)
    print("SQLAlchemy initialized with app.")
    # Ensure database tables, instance path, and upload directory exist on startup (supports Gunicorn & local dev)
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    with app.app_context():
        db.create_all()
        _ensure_user_profile_columns()
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

@app.template_filter('item_image_url')
def item_image_url_filter(image_filename):
    """Returns direct cloud URL if stored remotely, else local /uploads/ route."""
    if not image_filename:
        return ''
    if image_filename.startswith(('http://', 'https://')):
        return image_filename
    return url_for('uploaded_file', filename=image_filename)

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
        stored_ref = None
        is_temp_local = False
        try:
            stored_ref, filepath, is_temp_local = save_found_image(file, app.config['UPLOAD_FOLDER'])
            print(f"Image saved as: {stored_ref}")

            ai_description = generate_description_gemini(filepath)
            if ai_description.startswith("Error:"): flash(f'AI description failed: {ai_description}. Item saved.', 'warning'); ai_description = "AI description generation failed."

            user_description = (request.form.get('description') or '').strip()
            if user_description:
                if ai_description == "AI description generation failed.":
                    ai_description = user_description
                else:
                    ai_description = f"{ai_description}\n\nFinder notes: {user_description}"

            color = request.form.get('color')
            brand = request.form.get('brand')
            new_item = Item(
                status='found',
                item_type=item_type,
                color=color,
                brand=brand,
                location=location,
                image_filename=stored_ref,
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
            if stored_ref:
                delete_item_image(stored_ref, app.config['UPLOAD_FOLDER'])
            return render_template('report_found.html', item_types=item_types, entered_data=request.form) # Return to form
        finally:
            if is_temp_local and filepath and os.path.exists(filepath):
                try: os.remove(filepath)
                except OSError: pass

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

            user_description = (request.form.get('description') or '').strip()
            if user_description:
                lost_ai_description = (
                    f"{lost_ai_description}\nUser notes: {user_description}".strip()
                    if lost_ai_description
                    else user_description
                )

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

# --- User Authentication Routes ---

@app.route('/login', methods=['GET', 'POST'])
def user_login_page():
    """Handles user sign-in."""
    if session.get('user_id'):
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            session['user_name'] = user.name
            session['user_email'] = user.email
            session['user_avatar'] = user.avatar_url
            flash(f'Welcome back, {user.name}!', 'success')
            if not user.is_profile_complete:
                return redirect(url_for('user_profile_page', setup=1))
            next_url = request.args.get('next')
            if next_url and next_url.startswith('/') and not next_url.startswith('//'):
                return redirect(next_url)
            return redirect(url_for('index'))

        flash('Invalid email or password. Please try again.', 'danger')
        return render_template('auth.html', mode='login', entered_email=email, google_client_id=GOOGLE_CLIENT_ID)

    return render_template('auth.html', mode='login', entered_email='', google_client_id=GOOGLE_CLIENT_ID)


@app.route('/register', methods=['GET', 'POST'])
def user_register_page():
    """Handles new user sign-up."""
    if session.get('user_id'):
        return redirect(url_for('index'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not name or not email or not password:
            flash('Full name, email, and password are required.', 'danger')
            return render_template('auth.html', mode='signup', entered_name=name, entered_email=email, google_client_id=GOOGLE_CLIENT_ID)
        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            return render_template('auth.html', mode='signup', entered_name=name, entered_email=email, google_client_id=GOOGLE_CLIENT_ID)
        if User.query.filter_by(email=email).first():
            flash('An account with this email already exists. Please sign in.', 'warning')
            return render_template('auth.html', mode='login', entered_email=email, google_client_id=GOOGLE_CLIENT_ID)

        user = User(
            name=name,
            email=email,
            password_hash=generate_password_hash(password),
        )
        db.session.add(user)
        db.session.commit()

        session['user_id'] = user.id
        session['user_name'] = user.name
        session['user_email'] = user.email
        session['user_avatar'] = user.avatar_url
        flash(f'Account created! Please complete your private profile details.', 'info')
        return redirect(url_for('user_profile_page', setup=1))

    return render_template('auth.html', mode='signup', entered_name='', entered_email='', google_client_id=GOOGLE_CLIENT_ID)


@app.route('/auth/google', methods=['POST'])
def user_google_web_login():
    """Verifies Google Identity Services credential token and signs in the web user."""
    credential = request.form.get('credential', '').strip()
    idinfo = verify_google_id_token(credential)
    if not idinfo:
        flash('Google Sign-In failed. Please try again.', 'danger')
        return redirect(url_for('user_login_page'))

    user = get_or_create_google_user(idinfo)
    session['user_id'] = user.id
    session['user_name'] = user.name
    session['user_email'] = user.email
    session['user_avatar'] = user.avatar_url
    if not user.is_profile_complete:
        flash(f'Welcome, {user.name}! Please complete your profile to continue.', 'info')
        return redirect(url_for('user_profile_page', setup=1))
    flash(f'Welcome back, {user.name}!', 'success')
    return redirect(url_for('index'))


@app.route('/profile', methods=['GET', 'POST'])
def user_profile_page():
    """Allows signed-in users to complete or update their private profile details."""
    user_id = session.get('user_id')
    if not user_id:
        flash('Please sign in to view or update your profile.', 'warning')
        return redirect(url_for('user_login_page', next='/profile'))

    user = db.session.get(User, user_id)
    if not user:
        session.clear()
        return redirect(url_for('user_login_page'))

    is_setup = request.args.get('setup') == '1' or not user.is_profile_complete

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        address = request.form.get('address', '').strip()
        city = request.form.get('city', '').strip() or None
        alternate_contact = request.form.get('alternate_contact', '').strip() or None

        if not name or not phone or not address:
            flash('Full Name, Mobile Number, and Stay Address are required.', 'danger')
            return render_template('profile.html', user=user, is_setup=is_setup)

        user.name = name
        user.phone = phone
        user.address = address
        user.city = city
        user.alternate_contact = alternate_contact
        db.session.commit()

        session['user_name'] = user.name
        session['user_avatar'] = user.avatar_url
        flash('Your profile has been saved securely.', 'success')
        return redirect(url_for('index') if request.form.get('from_setup') == '1' else url_for('user_profile_page'))

    return render_template('profile.html', user=user, is_setup=is_setup)


@app.route('/logout')
def user_logout_page():
    """Logs out the current user."""
    session.pop('user_id', None)
    session.pop('user_name', None)
    session.pop('user_email', None)
    session.pop('user_avatar', None)
    flash('You have been signed out.', 'info')
    return redirect(url_for('index'))

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
        delete_item_image(item.image_filename, app.config['UPLOAD_FOLDER'])

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
            if delete_item_image(item.image_filename, app.config['UPLOAD_FOLDER']):
                deleted_files_count += 1

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