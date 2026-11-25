import os
from datetime import datetime
from functools import wraps
from flask import Blueprint, current_app, jsonify, request, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from config import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    DESCRIPTION_SIMILARITY_THRESHOLD,
    IMAGE_SIMILARITY_THRESHOLD,
    METADATA_SIMILARITY_THRESHOLD,
)
from models import Item, User, db
from services import (
    ITEM_TYPES,
    allowed_file,
    find_matching_found_items,
    generate_description_gemini,
    get_or_create_google_user,
    item_to_dict,
    save_uploaded_image,
    verify_google_id_token,
)

api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")

TOKEN_SALT = "findit-admin-auth-v1"
TOKEN_MAX_AGE_SECONDS = 86400  # 24 hours


def _get_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=TOKEN_SALT)


def _generate_admin_token(username):
    return _get_serializer().dumps({"username": username, "role": "admin"})


def _verify_admin_token(token):
    try:
        data = _get_serializer().loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
        return data.get("role") == "admin"
    except (BadSignature, SignatureExpired):
        return False


def api_admin_required(f):
    """Protects JSON admin endpoints using Bearer token authentication."""

    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Authorization header."}), 401
        token = auth_header.split(" ", 1)[1].strip()
        if not token or not _verify_admin_token(token):
            return jsonify({"error": "Invalid or expired admin token."}), 401
        return f(*args, **kwargs)

    return decorated


def _build_image_url(filename):
    if not filename:
        return None
    return url_for("uploaded_file", filename=filename, _external=True)


@api_v1.route("/health", methods=["GET"])
def health():
    """Lightweight health and configuration endpoint (also wakes Render cold starts)."""
    return jsonify({
        "status": "ok",
        "service": "lost-and-found-api",
        "item_types": ITEM_TYPES,
        "thresholds": {
            "metadata": METADATA_SIMILARITY_THRESHOLD,
            "description": DESCRIPTION_SIMILARITY_THRESHOLD,
            "image": IMAGE_SIMILARITY_THRESHOLD,
        },
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }), 200


@api_v1.route("/items", methods=["GET"])
def list_items():
    """Lists reported items with optional status/item_type filters and pagination."""
    status = request.args.get("status", "").strip().lower()
    item_type = request.args.get("item_type", "").strip()
    limit = min(max(request.args.get("limit", default=50, type=int), 1), 100)
    offset = max(request.args.get("offset", default=0, type=int), 0)

    query = Item.query
    if status in ("lost", "found"):
        query = query.filter_by(status=status)
    if item_type:
        query = query.filter(Item.item_type.ilike(item_type))

    total = query.count()
    items = query.order_by(Item.timestamp.desc()).offset(offset).limit(limit).all()

    return jsonify({
        "items": [item_to_dict(item, _build_image_url(item.image_filename)) for item in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }), 200


@api_v1.route("/items/<int:item_id>", methods=["GET"])
def get_item(item_id):
    """Fetches a single item by ID."""
    item = db.session.get(Item, item_id)
    if not item:
        return jsonify({"error": f"Item {item_id} not found."}), 404
    return jsonify({"item": item_to_dict(item, _build_image_url(item.image_filename))}), 200


@api_v1.route("/report-found", methods=["POST"])
def report_found():
    """Creates a new found-item report from multipart/form-data."""
    if "item_image" not in request.files:
        return jsonify({"error": "No 'item_image' file provided."}), 400
    file = request.files["item_image"]
    if not file or not file.filename:
        return jsonify({"error": "No image selected for uploading."}), 400

    allowed_exts = current_app.config["ALLOWED_EXTENSIONS"]
    if not allowed_file(file.filename, allowed_exts):
        return jsonify({"error": "Invalid file type. Allowed types: png, jpg, jpeg, gif."}), 400

    item_type = (request.form.get("item_type") or "").strip()
    location = (request.form.get("location") or "").strip()
    contact_info = (request.form.get("contact_info") or "").strip()
    color = (request.form.get("color") or "").strip() or None
    brand = (request.form.get("brand") or "").strip() or None
    user_description = (request.form.get("description") or "").strip()

    if not all([item_type, location, contact_info]):
        return jsonify({"error": "item_type, location, and contact_info are required."}), 400

    filepath = None
    try:
        upload_folder = current_app.config["UPLOAD_FOLDER"]
        filename, filepath = save_uploaded_image(file, upload_folder, prefix="found")

        ai_description = generate_description_gemini(filepath)
        ai_warning = None
        if ai_description.startswith("Error:"):
            ai_warning = ai_description
            ai_description = "AI description generation failed."

        if user_description:
            if ai_description == "AI description generation failed.":
                ai_description = user_description
            else:
                ai_description = f"{ai_description}\n\nFinder notes: {user_description}"

        new_item = Item(
            status="found",
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

        return jsonify({
            "message": "Found item reported successfully.",
            "ai_warning": ai_warning,
            "item": item_to_dict(new_item, _build_image_url(new_item.image_filename)),
        }), 201
    except Exception as e:
        db.session.rollback()
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass
        return jsonify({"error": f"Failed to report found item: {e}"}), 500


@api_v1.route("/search-lost", methods=["POST"])
def search_lost():
    """Searches for matching found items using the 3-tier AI pipeline."""
    if "item_image" not in request.files:
        return jsonify({"error": "No 'item_image' file provided."}), 400
    file = request.files["item_image"]
    if not file or not file.filename:
        return jsonify({"error": "No image selected for searching."}), 400

    allowed_exts = current_app.config["ALLOWED_EXTENSIONS"]
    if not allowed_file(file.filename, allowed_exts):
        return jsonify({"error": "Invalid file type. Allowed types: png, jpg, jpeg, gif."}), 400

    item_type = (request.form.get("item_type") or "").strip()
    location = (request.form.get("location") or "").strip()
    color = (request.form.get("color") or "").strip().lower()
    brand = (request.form.get("brand") or "").strip().lower()
    user_description = (request.form.get("description") or "").strip()

    if not all([item_type, location]):
        return jsonify({"error": "item_type and location are required."}), 400

    search_filepath = None
    try:
        upload_folder = current_app.config["UPLOAD_FOLDER"]
        _, search_filepath = save_uploaded_image(file, upload_folder, prefix="search")
        lost_ai_description = generate_description_gemini(search_filepath)
        ai_warning = None
        if lost_ai_description.startswith("Error:"):
            ai_warning = lost_ai_description
            lost_ai_description = ""

        if user_description:
            lost_ai_description = (
                f"{lost_ai_description}\nUser notes: {user_description}".strip()
                if lost_ai_description
                else user_description
            )

        lost_item_details = {
            "item_type": item_type.lower(),
            "color": color,
            "brand": brand,
            "location": location.lower(),
            "ai_description": lost_ai_description,
            "image_path": search_filepath,
        }

        matches = find_matching_found_items(lost_item_details, upload_folder)

        serialized_matches = [
            {
                "confidence": m["confidence"],
                "scores": m["scores"],
                "item": item_to_dict(
                    m["found_item"],
                    _build_image_url(m["found_item"].image_filename),
                ),
            }
            for m in matches
        ]

        return jsonify({
            "search_description": lost_ai_description or "N/A",
            "ai_warning": ai_warning,
            "match_count": len(serialized_matches),
            "matches": serialized_matches,
        }), 200
    except Exception as e:
        return jsonify({"error": f"Search failed: {e}"}), 500
    finally:
        if search_filepath and os.path.exists(search_filepath):
            try:
                os.remove(search_filepath)
            except OSError:
                pass


@api_v1.route("/auth/register", methods=["POST"])
def user_register():
    """Registers a new user account and returns a signed Bearer token."""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not name or not email or not password:
        return jsonify({"error": "Name, email, and password are required."}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters."}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "An account with this email already exists."}), 409

    user = User(
        name=name,
        email=email,
        password_hash=generate_password_hash(password),
    )
    db.session.add(user)
    db.session.commit()

    token = _get_serializer().dumps({"user_id": user.id, "email": user.email, "role": "user"})
    return jsonify({
        "token": token,
        "expires_in": TOKEN_MAX_AGE_SECONDS,
        "user": user.to_dict(),
    }), 201


@api_v1.route("/auth/login", methods=["POST"])
def user_login():
    """Authenticates a user account and returns a signed Bearer token."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400

    user = User.query.filter_by(email=email).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"error": "Invalid email or password."}), 401

    token = _get_serializer().dumps({"user_id": user.id, "email": user.email, "role": "user"})
    return jsonify({
        "token": token,
        "expires_in": TOKEN_MAX_AGE_SECONDS,
        "user": user.to_dict(),
    }), 200


@api_v1.route("/auth/google", methods=["POST"])
def user_google_login():
    """Verifies a Google ID token from Android/Web and returns a signed Bearer token."""
    data = request.get_json(silent=True) or {}
    id_token_str = (data.get("id_token") or "").strip()
    if not id_token_str:
        return jsonify({"error": "id_token is required."}), 400

    idinfo = verify_google_id_token(id_token_str)
    if not idinfo:
        return jsonify({"error": "Invalid or unverified Google ID token."}), 401

    user = get_or_create_google_user(idinfo)
    token = _get_serializer().dumps({"user_id": user.id, "email": user.email, "role": "user"})
    return jsonify({
        "token": token,
        "expires_in": TOKEN_MAX_AGE_SECONDS,
        "user": user.to_dict(),
    }), 200


@api_v1.route("/admin/login", methods=["POST"])
def admin_login():
    """Authenticates admin credentials and returns a signed Bearer token."""
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        return jsonify({
            "token": _generate_admin_token(username),
            "expires_in": TOKEN_MAX_AGE_SECONDS,
        }), 200
    return jsonify({"error": "Invalid username or password."}), 401


@api_v1.route("/admin/items/<int:item_id>", methods=["DELETE"])
@api_admin_required
def admin_delete_item(item_id):
    """Deletes a single item and its uploaded image."""
    item = db.session.get(Item, item_id)
    if not item:
        return jsonify({"error": f"Item {item_id} not found."}), 404

    try:
        if item.image_filename:
            image_path = os.path.join(current_app.config["UPLOAD_FOLDER"], item.image_filename)
            if os.path.exists(image_path) and os.path.isfile(image_path):
                os.remove(image_path)

        db.session.delete(item)
        db.session.commit()
        return jsonify({"message": f"Item #{item_id} deleted successfully."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Error deleting item #{item_id}: {e}"}), 500


@api_v1.route("/admin/clear-all", methods=["POST"])
@api_admin_required
def admin_clear_all():
    """Deletes all items and their uploaded images."""
    try:
        items = Item.query.all()
        deleted_files_count = 0
        upload_folder = current_app.config["UPLOAD_FOLDER"]

        for item in items:
            if item.image_filename:
                image_path = os.path.join(upload_folder, item.image_filename)
                if os.path.exists(image_path) and os.path.isfile(image_path):
                    try:
                        os.remove(image_path)
                        deleted_files_count += 1
                    except OSError:
                        pass

        num_deleted = Item.query.delete()
        db.session.commit()
        return jsonify({
            "message": f"Cleared {num_deleted} items and {deleted_files_count} images.",
            "deleted_items": num_deleted,
            "deleted_files": deleted_files_count,
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Error clearing database: {e}"}), 500
