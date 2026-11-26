import io
import os
import re
import secrets
import time
import cloudinary
import cloudinary.uploader
import google.generativeai as genai
import requests
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from PIL import Image as PILImage
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename

from config import (
    GEMINI_TEXT_API_KEY,
    GEMINI_IMAGE_API_KEY,
    GEMINI_MODEL_NAME,
    GEMINI_TEMPERATURE,
    GOOGLE_CLIENT_ID,
    CLOUDINARY_URL,
    ALLOWED_EXTENSIONS,
    DESCRIPTION_SIMILARITY_THRESHOLD,
    METADATA_SIMILARITY_THRESHOLD,
    IMAGE_SIMILARITY_THRESHOLD,
)
from models import Item, User, db

if CLOUDINARY_URL:
    cloudinary.config(cloudinary_url=CLOUDINARY_URL)

ITEM_TYPES = [
    "Electronics",
    "Keys & Wallet",
    "Pets",
    "Bags & Luggage",
    "Documents",
    "Clothing",
    "Jewelry & Watch",
    "Other",
]

# --- Gemini API Initialization ---
text_model = None
vision_model = None

try:
    if GEMINI_TEXT_API_KEY and GEMINI_TEXT_API_KEY != "YOUR API KEY":
        genai.configure(api_key=GEMINI_TEXT_API_KEY)
    elif GEMINI_IMAGE_API_KEY and GEMINI_IMAGE_API_KEY != "YOUR API KEY":
        genai.configure(api_key=GEMINI_IMAGE_API_KEY)

    generation_config = {"temperature": GEMINI_TEMPERATURE}
    text_model = genai.GenerativeModel(GEMINI_MODEL_NAME, generation_config=generation_config)
    vision_model = genai.GenerativeModel(GEMINI_MODEL_NAME, generation_config=generation_config)
except Exception as e:
    print(f"ERROR initializing Gemini models ('{GEMINI_MODEL_NAME}'): {e}. AI features will likely fail.")


def allowed_file(filename, allowed_extensions=ALLOWED_EXTENSIONS):
    """Checks if the file extension is allowed."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_extensions


def optimize_image_for_ai(image_source, max_dim=1024):
    """
    Opens a local path or http(s) image URL and resizes it so max dimension <= max_dim.
    Reduces RAM and bandwidth on constrained hosts like Render.
    """
    if isinstance(image_source, str) and image_source.startswith(("http://", "https://")):
        resp = requests.get(image_source, timeout=20)
        resp.raise_for_status()
        img = PILImage.open(io.BytesIO(resp.content))
    else:
        img = PILImage.open(image_source)

    if img.mode != "RGB":
        img = img.convert("RGB")
    if max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim), PILImage.Resampling.LANCZOS)
    return img


def clean_ai_description(text):
    """Strips conversational preambles and closing remarks from Gemini output."""
    if not text:
        return text
    pattern = r"^(?:(?:To help|For a|Here is|Below is|Based on|Sure|Certainly|This is)[^\n:]*?(?::|\n+))\s*"
    cleaned = re.sub(pattern, "", str(text).strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\n+(?:Hope this helps|I hope this helps).*?$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def generate_description_gemini(image_path):
    """Generates a visual description for an item image using Gemini."""
    if not text_model:
        return "Error: Gemini text model not initialized."
    if not os.path.exists(image_path):
        return "Error: Image file not found."

    try:
        if GEMINI_TEXT_API_KEY and GEMINI_TEXT_API_KEY != "YOUR API KEY":
            genai.configure(api_key=GEMINI_TEXT_API_KEY)
        img = optimize_image_for_ai(image_path, max_dim=1024)
        prompt = (
            "Describe this item in detail for a lost and found platform. "
            "Focus on visual characteristics like type, color, material, shape, and any unique markings. "
            "Do NOT include any conversational preamble or introductory remarks like 'To help identify...', "
            "'Here is a description...', or 'For a lost and found listing'. "
            "Start directly with the item description."
        )
        response = text_model.generate_content([prompt, img])
        raw_description = (
            response.text.strip()
            if hasattr(response, "text") and response.text
            else "AI could not generate a description."
        )
        return clean_ai_description(raw_description)
    except Exception as e:
        error_message = f"Error generating AI description: {type(e).__name__}."
        if "API key" in str(e):
            error_message = "Error: Invalid/missing Gemini API key (Text)."
        elif "quota" in str(e).lower():
            error_message = "Error: Gemini API quota exceeded."
        elif "DeadlineExceeded" in str(e):
            error_message = "Error: Gemini API request timed out."
        elif "ResourceExhausted" in str(e):
            error_message = "Error: Gemini API resource exhausted."
        return error_message


def compare_descriptions_gemini(desc1, desc2):
    """Compares two descriptions for semantic similarity using Gemini."""
    if not text_model or not desc1 or not desc2:
        return 0.0

    try:
        if GEMINI_TEXT_API_KEY and GEMINI_TEXT_API_KEY != "YOUR API KEY":
            genai.configure(api_key=GEMINI_TEXT_API_KEY)
        prompt = f"""
        On a scale of 0.0 to 1.0, how semantically similar are these descriptions?
        1: "{desc1}"
        2: "{desc2}"
        Respond ONLY with the numerical score (e.g., 0.75).
        """
        response = text_model.generate_content(prompt)
        match = re.search(r"[-+]?\d*\.\d+|\d+", response.text or "")
        if match:
            return max(0.0, min(1.0, float(match.group())))
        return 0.0
    except Exception as e:
        print(f"Error comparing descriptions with Gemini: {e}")
        return 0.0


def compare_images_gemini(image_source1, image_source2):
    """Compares two images (local paths or URLs) for visual similarity using Gemini Vision."""
    if not vision_model or not image_source1 or not image_source2:
        return 0.0
    for src in (image_source1, image_source2):
        if not src.startswith(("http://", "https://")) and not os.path.exists(src):
            return 0.0

    try:
        if GEMINI_IMAGE_API_KEY and GEMINI_IMAGE_API_KEY != "YOUR API KEY":
            genai.configure(api_key=GEMINI_IMAGE_API_KEY)
        img1 = optimize_image_for_ai(image_source1, max_dim=512)
        img2 = optimize_image_for_ai(image_source2, max_dim=512)
        prompt = """
        On a scale of 0.0 to 1.0, how visually similar are the items in these two images?
        Respond ONLY with the numerical score (e.g., 0.90).
        """
        response = vision_model.generate_content([prompt, img1, img2])
        match = re.search(r"[-+]?\d*\.\d+|\d+", response.text or "")
        if match:
            return max(0.0, min(1.0, float(match.group())))
        return 0.0
    except Exception as e:
        print(f"Error comparing images with Gemini: {e}")
        return 0.0


_CATEGORY_ALIASES = {
    "electronics": "electronics",
    "keys": "keys_wallet",
    "wallet/purse": "keys_wallet",
    "keys & wallet": "keys_wallet",
    "pet": "pets",
    "pets": "pets",
    "bag/backpack": "bags",
    "bags & luggage": "bags",
    "identification": "documents",
    "book/notebook": "documents",
    "documents": "documents",
    "clothing": "clothing",
    "jewelry/watch": "jewelry",
    "jewelry & watch": "jewelry",
    "other": "other",
}


def normalize_category(item_type):
    """Maps legacy web categories and unified Android categories to canonical groups."""
    key = (item_type or "").strip().lower()
    return _CATEGORY_ALIASES.get(key, key)


def calculate_metadata_similarity(item_meta1, item_meta2):
    """Calculates similarity based on item type, color, brand, and location."""
    score = 0.0
    max_possible_score = 0.0

    # Item Type (Normalized Group Match, High Weight)
    max_possible_score += 1.5
    cat1 = normalize_category(item_meta1.get("item_type"))
    cat2 = normalize_category(item_meta2.get("item_type"))
    if cat1 and cat1 == cat2:
        score += 1.5

    # Color (Partial Match)
    max_possible_score += 1.0
    if item_meta1.get("color") and item_meta1["color"] == item_meta2.get("color"):
        score += 1.0
    elif (
        item_meta1.get("color")
        and item_meta2.get("color")
        and (item_meta1["color"] in item_meta2["color"] or item_meta2["color"] in item_meta1["color"])
    ):
        score += 0.5

    # Brand (Partial Match, Bonus if both missing)
    max_possible_score += 1.0
    if item_meta1.get("brand") and item_meta1["brand"] == item_meta2.get("brand"):
        score += 1.0
    elif (
        item_meta1.get("brand")
        and item_meta2.get("brand")
        and (item_meta1["brand"] in item_meta2["brand"] or item_meta2["brand"] in item_meta1["brand"])
    ):
        score += 0.5
    elif not item_meta1.get("brand") and not item_meta2.get("brand"):
        score += 0.25

    # Location (Simple Substring Match)
    max_possible_score += 1.0
    if item_meta1.get("location") and item_meta1["location"] == item_meta2.get("location"):
        score += 1.0
    elif (
        item_meta1.get("location")
        and item_meta2.get("location")
        and (item_meta1["location"] in item_meta2["location"] or item_meta2["location"] in item_meta1["location"])
    ):
        score += 0.5

    return score / max_possible_score if max_possible_score > 0 else 0.0


def save_uploaded_image(file_storage, upload_folder, prefix="found"):
    """Saves an uploaded FileStorage safely and returns (filename, filepath)."""
    basefilename, file_extension = os.path.splitext(file_storage.filename or "")
    safe_base = secure_filename(basefilename) or prefix
    ext = file_extension.lower() if file_extension else ".jpg"
    filename = f"{prefix}_{int(time.time())}_{safe_base}{ext}"
    os.makedirs(upload_folder, exist_ok=True)
    filepath = os.path.join(upload_folder, filename)
    file_storage.save(filepath)
    return filename, filepath


def save_found_image(file_storage, upload_folder, prefix="found"):
    """
    Saves an item photo to Cloudinary (if CLOUDINARY_URL is configured) or local disk.
    Returns (stored_ref, local_filepath, is_temp_local).
    """
    filename, filepath = save_uploaded_image(file_storage, upload_folder, prefix=prefix)
    if CLOUDINARY_URL:
        result = cloudinary.uploader.upload(
            filepath,
            folder="lost_and_found",
            resource_type="image",
        )
        secure_url = result["secure_url"]
        return secure_url, filepath, True
    return filename, filepath, False


def resolve_image_source(image_filename, upload_folder):
    """Returns a URL or local filesystem path for an Item's stored image_filename."""
    if not image_filename:
        return None
    if image_filename.startswith(("http://", "https://")):
        return image_filename
    return os.path.join(upload_folder, image_filename)


def _extract_cloudinary_public_id(url):
    if "/upload/" not in url:
        return None
    right = url.split("/upload/", 1)[1]
    right = re.sub(r"^v\d+/", "", right)
    public_id, _ = os.path.splitext(right)
    return public_id or None


def delete_item_image(image_filename, upload_folder):
    """Deletes an item's image from Cloudinary or local disk. Returns True if deleted."""
    if not image_filename:
        return False
    if image_filename.startswith(("http://", "https://")):
        if not CLOUDINARY_URL:
            return False
        public_id = _extract_cloudinary_public_id(image_filename)
        if not public_id:
            return False
        try:
            res = cloudinary.uploader.destroy(public_id, resource_type="image")
            return res.get("result") in ("ok", "not found")
        except Exception as e:
            print(f"Cloudinary delete failed for {public_id}: {e}")
            return False
    image_path = os.path.join(upload_folder, image_filename)
    if os.path.exists(image_path) and os.path.isfile(image_path):
        try:
            os.remove(image_path)
            return True
        except OSError as e:
            print(f"Could not delete local image {image_path}: {e}")
    return False


def _find_matching_items(query_details, upload_folder, target_status="found"):
    """
    Runs the three-tier matching pipeline against all items with status == target_status.
    Returns a list of match dicts sorted by confidence descending.
    """
    candidates = Item.query.filter_by(status=target_status).all()
    matches = []

    for candidate in candidates:
        match_scores = {"description": 0.0, "metadata": 0.0, "image": 0.0, "confidence": 0.0}

        # Tier 1: Metadata Heuristics (0 API calls)
        meta_similarity = calculate_metadata_similarity(query_details, candidate.get_metadata())
        match_scores["metadata"] = round(meta_similarity, 4)
        if meta_similarity < METADATA_SIMILARITY_THRESHOLD:
            continue

        # Tier 2: Semantic Description Similarity (Gemini Text)
        desc_similarity = 0.0
        if (
            query_details.get("ai_description")
            and candidate.ai_description
            and "failed" not in candidate.ai_description.lower()
        ):
            desc_similarity = compare_descriptions_gemini(
                query_details["ai_description"], candidate.ai_description
            )
            match_scores["description"] = round(desc_similarity, 4)

        if desc_similarity < DESCRIPTION_SIMILARITY_THRESHOLD:
            continue

        # Tier 3: Direct Visual Similarity (Gemini Vision) — only when a query image is provided
        if query_details.get("image_path"):
            img_similarity = 0.0
            candidate_image_source = resolve_image_source(candidate.image_filename, upload_folder)
            if candidate_image_source and (
                candidate_image_source.startswith(("http://", "https://")) or os.path.exists(candidate_image_source)
            ):
                img_similarity = compare_images_gemini(query_details["image_path"], candidate_image_source)
                match_scores["image"] = round(img_similarity, 4)

            if img_similarity < IMAGE_SIMILARITY_THRESHOLD:
                continue

            confidence = (desc_similarity * 0.3) + (meta_similarity * 0.3) + (img_similarity * 0.4)
        else:
            # Text-only search fallback (no reference photo uploaded)
            if desc_similarity < max(DESCRIPTION_SIMILARITY_THRESHOLD, 0.55):
                continue
            confidence = (desc_similarity * 0.65) + (meta_similarity * 0.35)
        match_scores["confidence"] = round(confidence, 4)
        matches.append({
            "found_item": candidate,
            "matched_item": candidate,
            "scores": match_scores,
            "confidence": round(confidence, 4),
        })

    matches.sort(key=lambda x: x["confidence"], reverse=True)
    return matches


def find_matching_found_items(lost_item_details, upload_folder):
    """Runs the three-tier matching pipeline against all 'found' items in the DB."""
    return _find_matching_items(lost_item_details, upload_folder, target_status="found")


def find_matching_lost_items(found_item_details, upload_folder):
    """Runs the three-tier matching pipeline against all 'lost' items in the DB."""
    return _find_matching_items(found_item_details, upload_folder, target_status="lost")


def item_to_dict(item, image_url=None):
    """Serializes an Item model instance to a JSON-compatible dict."""
    return {
        "id": item.id,
        "status": item.status,
        "item_type": item.item_type,
        "color": item.color,
        "brand": item.brand,
        "location": item.location,
        "image_filename": item.image_filename,
        "image_url": image_url,
        "ai_description": item.ai_description,
        "contact_info": item.contact_info,
        "timestamp": item.timestamp.isoformat() + "Z" if item.timestamp else None,
    }


def verify_google_id_token(token):
    """Verifies a Google ID token against GOOGLE_CLIENT_ID and returns its claims."""
    if not token or not GOOGLE_CLIENT_ID:
        return None
    try:
        idinfo = google_id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
        if not idinfo.get("email") or not idinfo.get("email_verified"):
            return None
        return idinfo
    except Exception as e:
        print(f"Google ID token verification failed: {e}")
        return None


def get_or_create_google_user(idinfo):
    """Finds an existing User by email or creates one for a verified Google account."""
    email = idinfo["email"].strip().lower()
    picture = (idinfo.get("picture") or "").strip() or None
    user = User.query.filter_by(email=email).first()
    if user:
        if picture and user.avatar_url != picture:
            user.avatar_url = picture
            db.session.commit()
        return user
    name = (idinfo.get("name") or email.split("@")[0]).strip()
    user = User(
        name=name,
        email=email,
        avatar_url=picture,
        password_hash=generate_password_hash(secrets.token_urlsafe(32)),
    )
    db.session.add(user)
    db.session.commit()
    return user
