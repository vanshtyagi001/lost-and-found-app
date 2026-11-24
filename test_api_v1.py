import io
import os
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
    assert data["thresholds"]["image"] == 0.80


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

    # Temporary search image must be deleted, leaving only the found item image
    upload_files = os.listdir(app.config["UPLOAD_FOLDER"])
    assert len(upload_files) == 1
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
