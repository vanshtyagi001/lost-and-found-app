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
from models import Item, Notification, User, db
from services import (
    ITEM_TYPES,
    allowed_file,
    delete_item_image,
    find_matching_found_items,
    find_matching_lost_items,
    generate_description_gemini,
    get_or_create_google_user,
    item_to_dict,
    save_found_image,
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


def _verify_user_token(token):
    try:
        data = _get_serializer().loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
        if data.get("role") == "user" and data.get("user_id"):
            return data["user_id"]
        return None
    except (BadSignature, SignatureExpired):
        return None


def _get_optional_user():
    """Returns the authenticated User if a valid Bearer token is provided, else None."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    user_id = _verify_user_token(token) if token else None
    return db.session.get(User, user_id) if user_id else None


def api_user_required(f):
    """Protects JSON user endpoints using Bearer token authentication."""

    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Authorization header."}), 401
        token = auth_header.split(" ", 1)[1].strip()
        user_id = _verify_user_token(token) if token else None
        if not user_id:
            return jsonify({"error": "Invalid or expired user token."}), 401
        user = db.session.get(User, user_id)
        if not user:
            return jsonify({"error": "User not found."}), 401
        return f(user, *args, **kwargs)

    return decorated


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
    if filename.startswith(("http://", "https://")):
        return filename
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
    stored_ref = None
    is_temp_local = False
    try:
        upload_folder = current_app.config["UPLOAD_FOLDER"]
        stored_ref, filepath, is_temp_local = save_found_image(file, upload_folder)

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

        current_user = _get_optional_user()
        new_item = Item(
            status="found",
            item_type=item_type,
            color=color,
            brand=brand,
            location=location,
            image_filename=stored_ref,
            ai_description=ai_description,
            contact_info=contact_info,
            user_id=current_user.id if current_user else None,
        )
        db.session.add(new_item)
        db.session.commit()

        # Check if this newly found item matches any previously reported lost items
        found_item_details = {
            "item_type": item_type.lower(),
            "color": (color or "").lower(),
            "brand": (brand or "").lower(),
            "location": location.lower(),
            "ai_description": ai_description,
            "image_path": filepath,
        }
        lost_matches = find_matching_lost_items(found_item_details, upload_folder)
        serialized_lost_matches = []
        item_label = f"{brand} {item_type}".strip() if brand else item_type

        for m in lost_matches:
            lost_item = m["matched_item"]
            serialized_lost_matches.append({
                "confidence": m["confidence"],
                "scores": m["scores"],
                "item": item_to_dict(
                    lost_item,
                    _build_image_url(lost_item.image_filename),
                ),
            })
            if lost_item.user_id:
                confidence_pct = round(m["confidence"] * 100)
                notif = Notification(
                    user_id=lost_item.user_id,
                    found_item_id=new_item.id,
                    lost_item_id=lost_item.id,
                    title=f"Possible Match Found: {item_label}",
                    message=(
                        f"Great news! A {item_label} matching your lost report was just "
                        f"found near {location} ({confidence_pct}% match). "
                        f"Tap to view details and contact the finder."
                    ),
                    confidence=m["confidence"],
                )
                db.session.add(notif)

        if lost_matches:
            db.session.commit()

        return jsonify({
            "message": "Found item reported successfully.",
            "ai_warning": ai_warning,
            "item": item_to_dict(new_item, _build_image_url(new_item.image_filename)),
            "lost_match_count": len(serialized_lost_matches),
            "lost_matches": serialized_lost_matches,
        }), 201
    except Exception as e:
        db.session.rollback()
        if stored_ref:
            delete_item_image(stored_ref, current_app.config["UPLOAD_FOLDER"])
        return jsonify({"error": f"Failed to report found item: {e}"}), 500
    finally:
        if is_temp_local and filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass


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
    current_user = _get_optional_user()
    contact_info = (request.form.get("contact_info") or "").strip()
    if not contact_info and current_user:
        contact_info = (current_user.phone or current_user.email or "").strip()
    if not contact_info:
        contact_info = "Not provided"

    if not all([item_type, location]):
        return jsonify({"error": "item_type and location are required."}), 400

    search_filepath = None
    stored_ref = None
    is_temp_local = False
    try:
        upload_folder = current_app.config["UPLOAD_FOLDER"]
        stored_ref, search_filepath, is_temp_local = save_found_image(file, upload_folder, prefix="lost")
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

        lost_item = Item(
            status="lost",
            item_type=item_type,
            color=color or None,
            brand=brand or None,
            location=location,
            image_filename=stored_ref,
            ai_description=lost_ai_description or None,
            contact_info=contact_info,
            user_id=current_user.id if current_user else None,
        )
        db.session.add(lost_item)
        db.session.commit()

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
            "lost_item": item_to_dict(lost_item, _build_image_url(lost_item.image_filename)),
        }), 200
    except Exception as e:
        db.session.rollback()
        if stored_ref:
            delete_item_image(stored_ref, current_app.config["UPLOAD_FOLDER"])
        return jsonify({"error": f"Search failed: {e}"}), 500
    finally:
        if is_temp_local and search_filepath and os.path.exists(search_filepath):
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


@api_v1.route("/auth/profile", methods=["GET", "PUT"])
@api_user_required
def user_profile_api(user):
    """Fetches or updates the signed-in user's private profile details."""
    if request.method == "GET":
        return jsonify({"user": user.to_dict()}), 200

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    address = (data.get("address") or "").strip()
    city = (data.get("city") or "").strip() or None
    alternate_contact = (data.get("alternate_contact") or "").strip() or None

    if not name or not phone or not address:
        return jsonify({"error": "Name, mobile number, and stay address are required."}), 400

    user.name = name
    user.phone = phone
    user.address = address
    user.city = city
    user.alternate_contact = alternate_contact
    db.session.commit()

    return jsonify({
        "message": "Your profile has been saved securely.",
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
        delete_item_image(item.image_filename, current_app.config["UPLOAD_FOLDER"])
        Notification.query.filter(
            (Notification.found_item_id == item.id) | (Notification.lost_item_id == item.id)
        ).delete(synchronize_session=False)
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
            if delete_item_image(item.image_filename, upload_folder):
                deleted_files_count += 1

        Notification.query.delete()
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


@api_v1.route("/notifications", methods=["GET"])
@api_user_required
def list_notifications(user):
    """Returns match notifications for the authenticated user, newest first."""
    notifs = (
        Notification.query.filter_by(user_id=user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(50)
        .all()
    )
    found_ids = {n.found_item_id for n in notifs if n.found_item_id}
    found_map = {}
    if found_ids:
        for item in Item.query.filter(Item.id.in_(found_ids)).all():
            found_map[item.id] = item_to_dict(item, _build_image_url(item.image_filename))

    serialized = [n.to_dict(found_item=found_map.get(n.found_item_id)) for n in notifs]
    unread_count = sum(1 for n in notifs if not n.is_read)
    return jsonify({
        "notifications": serialized,
        "unread_count": unread_count,
    }), 200


@api_v1.route("/notifications/<int:notif_id>/read", methods=["POST"])
@api_user_required
def mark_notification_read(user, notif_id):
    """Marks a single notification as read for the authenticated user."""
    notif = Notification.query.filter_by(id=notif_id, user_id=user.id).first()
    if not notif:
        return jsonify({"error": "Notification not found."}), 404
    notif.is_read = True
    db.session.commit()
    return jsonify({"message": "Notification marked as read."}), 200


@api_v1.route("/notifications/read-all", methods=["POST"])
@api_user_required
def mark_all_notifications_read(user):
    """Marks all notifications as read for the authenticated user."""
    Notification.query.filter_by(user_id=user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    return jsonify({"message": "All notifications marked as read."}), 200
