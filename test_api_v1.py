import io
import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
import pytest
from PIL import Image as PILImage

import api_v1
import services
from app import app, db
from config import ADMIN_PASSWORD, ADMIN_USERNAME
from models import Item


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Provides an isolated Flask test client with a temporary DB and upload folder."""
    db_path = tmp_path / "test_items.db"
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    app.config["UPLOAD_FOLDER"] = str(upload_dir)
    monkeypatch.setattr(services, "CLOUDINARY_URL", "")

    with app.app_context():
        db.drop_all()
        db.create_all()

    with app.test_client() as test_client:
        yield test_client

    with app.app_context():
        db.session.remove()
        db.drop_all()


def _make_jpeg_bytes(color=(30, 144, 255)):
    buf = io.BytesIO()
    img = PILImage.new("RGB", (64, 64), color=color)
    img.save(buf, format="JPEG")
    buf.seek(0)
    return buf


def test_health_endpoint(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert "Electronics" in data["item_types"]
    assert data["thresholds"]["image"] == 0.70


def test_report_found_and_list_items(client, monkeypatch):
    monkeypatch.setattr(
        api_v1,
        "generate_description_gemini",
        lambda path: "Matte black wireless earbuds charging case.",
    )

    resp = client.post(
        "/api/v1/report-found",
        data={
            "item_type": "Electronics",
            "color": "Black",
            "brand": "Sony",
            "location": "Main Library",
            "contact_info": "finder@example.com",
            "item_image": (_make_jpeg_bytes(), "earbuds.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201
    created = resp.get_json()
    assert created["item"]["item_type"] == "Electronics"
    assert created["item"]["ai_description"] == "Matte black wireless earbuds charging case."
    assert created["item"]["image_url"].endswith(created["item"]["image_filename"])

    list_resp = client.get("/api/v1/items?status=found")
    assert list_resp.status_code == 200
    list_data = list_resp.get_json()
    assert list_data["total"] == 1
    assert list_data["items"][0]["id"] == created["item"]["id"]

    single_resp = client.get(f"/api/v1/items/{created['item']['id']}")
    assert single_resp.status_code == 200
    assert single_resp.get_json()["item"]["brand"] == "Sony"


def test_report_found_validation_errors(client):
    resp = client.post(
        "/api/v1/report-found",
        data={"item_type": "Keys"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "error" in resp.get_json()

    resp_bad_ext = client.post(
        "/api/v1/report-found",
        data={
            "item_type": "Keys",
            "location": "Cafe",
            "contact_info": "test@example.com",
            "item_image": (io.BytesIO(b"not-an-image"), "notes.txt"),
        },
        content_type="multipart/form-data",
    )
    assert resp_bad_ext.status_code == 400


def test_search_lost_three_tier_match_and_cleanup(client, monkeypatch):
    monkeypatch.setattr(
        api_v1,
        "generate_description_gemini",
        lambda path: "Blue leather bi-fold wallet with silver zipper.",
    )
    monkeypatch.setattr(services, "compare_descriptions_gemini", lambda d1, d2: 0.90)
    monkeypatch.setattr(services, "compare_images_gemini", lambda p1, p2: 0.92)

    report_resp = client.post(
        "/api/v1/report-found",
        data={
            "item_type": "Wallet/Purse",
            "color": "Blue",
            "brand": "Fossil",
            "location": "Cafeteria",
            "contact_info": "desk@example.com",
            "item_image": (_make_jpeg_bytes((0, 0, 200)), "found_wallet.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert report_resp.status_code == 201

    search_resp = client.post(
        "/api/v1/search-lost",
        data={
            "item_type": "Wallet/Purse",
            "color": "Blue",
            "brand": "Fossil",
            "location": "Cafeteria",
            "item_image": (_make_jpeg_bytes((0, 0, 210)), "lost_wallet.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert search_resp.status_code == 200
    data = search_resp.get_json()
    assert data["match_count"] == 1
    assert data["matches"][0]["confidence"] > 0.85
    assert data["matches"][0]["item"]["contact_info"] == "desk@example.com"

    # Both the found item image and the stored lost item image should be present
    upload_files = os.listdir(app.config["UPLOAD_FOLDER"])
    assert len(upload_files) == 2
    assert data["lost_item"]["status"] == "lost"
    assert upload_files[0].startswith("found_")


def test_search_lost_requires_image_and_accepts_optional_description(client, monkeypatch):
    monkeypatch.setattr(
        api_v1,
        "generate_description_gemini",
        lambda path: "Silver keychain with two brass keys.",
    )
    monkeypatch.setattr(services, "compare_descriptions_gemini", lambda d1, d2: 0.88)
    monkeypatch.setattr(services, "compare_images_gemini", lambda p1, p2: 0.90)

    client.post(
        "/api/v1/report-found",
        data={
            "item_type": "Keys",
            "color": "Silver",
            "location": "Gym",
            "contact_info": "gym@example.com",
            "description": "Found near locker 12",
            "item_image": (_make_jpeg_bytes(), "keys.jpg"),
        },
        content_type="multipart/form-data",
    )

    # Missing image is rejected
    no_img_resp = client.post(
        "/api/v1/search-lost",
        data={
            "item_type": "Keys",
            "color": "Silver",
            "location": "Gym",
            "description": "Silver keychain with two keys",
        },
        content_type="multipart/form-data",
    )
    assert no_img_resp.status_code == 400

    # Image + optional description succeeds
    resp = client.post(
        "/api/v1/search-lost",
        data={
            "item_type": "Keys",
            "color": "Silver",
            "location": "Gym",
            "description": "Has a small blue tag",
            "item_image": (_make_jpeg_bytes(), "lost_keys.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["match_count"] == 1
    assert "Has a small blue tag" in data["search_description"]


def test_admin_token_auth_delete_and_clear(client, monkeypatch):
    monkeypatch.setattr(api_v1, "generate_description_gemini", lambda path: "Silver house key.")

    client.post(
        "/api/v1/report-found",
        data={
            "item_type": "Keys",
            "location": "Parking Lot",
            "contact_info": "sec@example.com",
            "item_image": (_make_jpeg_bytes(), "key1.jpg"),
        },
        content_type="multipart/form-data",
    )
    client.post(
        "/api/v1/report-found",
        data={
            "item_type": "Keys",
            "location": "Gym",
            "contact_info": "gym@example.com",
            "item_image": (_make_jpeg_bytes(), "key2.jpg"),
        },
        content_type="multipart/form-data",
    )

    # Unauthenticated delete rejected
    unauth = client.delete("/api/v1/admin/items/1")
    assert unauth.status_code == 401

    # Invalid login rejected
    bad_login = client.post(
        "/api/v1/admin/login",
        json={"username": ADMIN_USERNAME, "password": "wrong-password"},
    )
    assert bad_login.status_code == 401

    # Valid login returns token
    login_resp = client.post(
        "/api/v1/admin/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert login_resp.status_code == 200
    token = login_resp.get_json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    del_resp = client.delete("/api/v1/admin/items/1", headers=headers)
    assert del_resp.status_code == 200
    assert client.get("/api/v1/items/1").status_code == 404

    clear_resp = client.post("/api/v1/admin/clear-all", headers=headers)
    assert clear_resp.status_code == 200
    assert clear_resp.get_json()["deleted_items"] == 1
    assert os.listdir(app.config["UPLOAD_FOLDER"]) == []


def test_user_register_and_login(client):
    reg_resp = client.post(
        "/api/v1/auth/register",
        json={"name": "Vansh Tyagi", "email": "vansh@example.com", "password": "secret123"},
    )
    assert reg_resp.status_code == 201
    reg_data = reg_resp.get_json()
    assert reg_data["user"]["email"] == "vansh@example.com"
    assert reg_data["token"]

    dup_resp = client.post(
        "/api/v1/auth/register",
        json={"name": "Vansh", "email": "vansh@example.com", "password": "secret123"},
    )
    assert dup_resp.status_code == 409

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "vansh@example.com", "password": "secret123"},
    )
    assert login_resp.status_code == 200
    assert login_resp.get_json()["user"]["name"] == "Vansh Tyagi"


def test_category_normalization_matches_legacy_and_android_labels(client, monkeypatch):
    monkeypatch.setattr(api_v1, "generate_description_gemini", lambda path: "Brown leather wallet.")
    monkeypatch.setattr(services, "compare_descriptions_gemini", lambda d1, d2: 0.85)
    monkeypatch.setattr(services, "compare_images_gemini", lambda p1, p2: 0.75)

    # Legacy web label 'Wallet/Purse'
    client.post(
        "/api/v1/report-found",
        data={
            "item_type": "Wallet/Purse",
            "location": "North Gate",
            "contact_info": "guard@example.com",
            "item_image": (_make_jpeg_bytes(), "wallet.jpg"),
        },
        content_type="multipart/form-data",
    )

    # Unified Android label 'Keys & Wallet' with differently worded location
    resp = client.post(
        "/api/v1/search-lost",
        data={
            "item_type": "Keys & Wallet",
            "location": "Campus Entrance",
            "item_image": (_make_jpeg_bytes(), "lost_wallet.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert resp.get_json()["match_count"] == 1


def test_web_navbar_user_auth_and_direct_admin_route(client):
    home = client.get("/")
    assert home.status_code == 200
    html = home.get_data(as_text=True)
    assert "Sign In" in html
    assert "Sign Up" not in html
    assert 'href="/admin"' not in html

    # Direct /admin route still works (redirects to /admin/login)
    admin_resp = client.get("/admin")
    assert admin_resp.status_code == 302
    assert "/admin/login" in admin_resp.headers["Location"]

    # Web sign-up redirects to profile setup, and existing users can update /profile
    reg = client.post(
        "/register",
        data={"name": "Alice", "email": "alice@example.com", "password": "password123"},
        follow_redirects=True,
    )
    assert reg.status_code == 200
    reg_html = reg.get_data(as_text=True)
    assert "Complete Your Profile" in reg_html
    assert "strictly private" in reg_html

    prof = client.post(
        "/profile",
        data={
            "name": "Alice",
            "phone": "+919876543210",
            "address": "Hostel A, Room 101",
            "city": "Delhi",
            "from_setup": "1",
        },
        follow_redirects=True,
    )
    assert prof.status_code == 200
    assert "My Profile" in prof.get_data(as_text=True)

    logout = client.get("/logout", follow_redirects=True)
    assert logout.status_code == 200
    assert "Sign In" in logout.get_data(as_text=True)


def test_google_signin_api_and_web(client, monkeypatch):
    monkeypatch.setattr(
        api_v1,
        "verify_google_id_token",
        lambda token: {
            "email": "googleuser@example.com",
            "email_verified": True,
            "name": "Google User",
            "picture": "https://lh3.googleusercontent.com/a/avatar.jpg",
        } if token == "valid-google-token" else None,
    )
    import app as flask_app
    monkeypatch.setattr(
        flask_app,
        "verify_google_id_token",
        lambda token: {
            "email": "googleuser@example.com",
            "email_verified": True,
            "name": "Google User",
            "picture": "https://lh3.googleusercontent.com/a/avatar.jpg",
        } if token == "valid-google-token" else None,
    )

    bad = client.post("/api/v1/auth/google", json={"id_token": "bad"})
    assert bad.status_code == 401

    good = client.post("/api/v1/auth/google", json={"id_token": "valid-google-token"})
    assert good.status_code == 200
    data = good.get_json()
    assert data["user"]["email"] == "googleuser@example.com"
    assert data["user"]["avatar_url"] == "https://lh3.googleusercontent.com/a/avatar.jpg"
    assert data["token"]

    web_resp = client.post(
        "/auth/google",
        data={"credential": "valid-google-token"},
        follow_redirects=True,
    )
    assert web_resp.status_code == 200
    web_html = web_resp.get_data(as_text=True)
    assert "Google User" in web_html
    assert "https://lh3.googleusercontent.com/a/avatar.jpg" in web_html
    assert "Complete Your Profile" in web_html

    # Update user profile via /api/v1/auth/profile
    prof_resp = client.put(
        "/api/v1/auth/profile",
        headers={"Authorization": f"Bearer {data['token']}"},
        json={
            "name": "Google User Updated",
            "phone": "+919876543210",
            "address": "Hostel C, Room 12",
            "city": "Noida",
        },
    )
    assert prof_resp.status_code == 200
    prof_user = prof_resp.get_json()["user"]
    assert prof_user["profile_complete"] is True
    assert prof_user["phone"] == "+919876543210"
    assert prof_user["email"] == "googleuser@example.com"


def test_cloudinary_upload_and_delete(client, monkeypatch):
    monkeypatch.setattr(services, "CLOUDINARY_URL", "cloudinary://key:secret@cloud")
    monkeypatch.setattr(api_v1, "generate_description_gemini", lambda path: "Red umbrella.")
    destroyed = []

    monkeypatch.setattr(
        services.cloudinary.uploader,
        "upload",
        lambda path, **kwargs: {
            "secure_url": "https://res.cloudinary.com/cloud/image/upload/v12345/lost_and_found/umbrella.jpg"
        },
    )
    monkeypatch.setattr(
        services.cloudinary.uploader,
        "destroy",
        lambda public_id, **kwargs: destroyed.append(public_id) or {"result": "ok"},
    )

    resp = client.post(
        "/api/v1/report-found",
        data={
            "item_type": "Other",
            "location": "Main Gate",
            "contact_info": "desk@example.com",
            "item_image": (_make_jpeg_bytes((200, 0, 0)), "umbrella.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201
    item = resp.get_json()["item"]
    assert item["image_url"] == "https://res.cloudinary.com/cloud/image/upload/v12345/lost_and_found/umbrella.jpg"
    assert os.listdir(app.config["UPLOAD_FOLDER"]) == []

    login_resp = client.post(
        "/api/v1/admin/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    token = login_resp.get_json()["token"]
    del_resp = client.delete(
        f"/api/v1/admin/items/{item['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del_resp.status_code == 200
    assert destroyed == ["lost_and_found/umbrella"]


def test_lost_report_reverse_match_and_notification(client, monkeypatch):
    monkeypatch.setattr(
        api_v1,
        "generate_description_gemini",
        lambda path: "Black wireless mouse with RGB scroll wheel.",
    )
    monkeypatch.setattr(services, "compare_descriptions_gemini", lambda d1, d2: 0.91)
    monkeypatch.setattr(services, "compare_images_gemini", lambda p1, p2: 0.93)

    # 1. User A registers and reports/searches a lost item
    reg = client.post(
        "/api/v1/auth/register",
        json={"name": "Alice", "email": "alice@example.com", "password": "password123"},
    )
    assert reg.status_code == 201
    alice_token = reg.get_json()["token"]

    lost_resp = client.post(
        "/api/v1/search-lost",
        headers={"Authorization": f"Bearer {alice_token}"},
        data={
            "item_type": "Electronics",
            "color": "Black",
            "brand": "Zebronics",
            "location": "Block 28",
            "item_image": (_make_jpeg_bytes((20, 20, 20)), "lost_mouse.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert lost_resp.status_code == 200
    assert lost_resp.get_json()["lost_item"]["status"] == "lost"

    # 2. User B reports a matching found item
    found_resp = client.post(
        "/api/v1/report-found",
        data={
            "item_type": "Electronics",
            "color": "Black",
            "brand": "Zebronics",
            "location": "Block 28",
            "contact_info": "+919876543210",
            "item_image": (_make_jpeg_bytes((25, 25, 25)), "found_mouse.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert found_resp.status_code == 201
    found_data = found_resp.get_json()
    assert found_data["lost_match_count"] == 1

    # 3. User A receives an unread match notification with the found item embedded
    notif_resp = client.get(
        "/api/v1/notifications",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert notif_resp.status_code == 200
    notif_data = notif_resp.get_json()
    assert notif_data["unread_count"] == 1
    notif = notif_data["notifications"][0]
    assert "Match Found" in notif["title"]
    assert notif["found_item"]["contact_info"] == "+919876543210"

    # 4. Mark notification as read
    read_resp = client.post(
        f"/api/v1/notifications/{notif['id']}/read",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert read_resp.status_code == 200
    after_resp = client.get(
        "/api/v1/notifications",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert after_resp.get_json()["unread_count"] == 0
