from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import secrets

db = SQLAlchemy()

_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_item_reference_id(status):
    """Generates an industry-standard prefixed reference ID (LST-YYYYMMDD-XXXXXX / FND-YYYYMMDD-XXXXXX)."""
    prefix = "LST" if (status or "").strip().lower() == "lost" else "FND"
    date_part = datetime.utcnow().strftime("%Y%m%d")
    rand_part = "".join(secrets.choice(_REF_ALPHABET) for _ in range(6))
    return f"{prefix}-{date_part}-{rand_part}"


class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reference_id = db.Column(db.String(32), unique=True, nullable=True, index=True)
    status = db.Column(db.String(10), nullable=False) # 'lost' or 'found'
    item_type = db.Column(db.String(100), nullable=False)
    color = db.Column(db.String(50), nullable=True)
    brand = db.Column(db.String(100), nullable=True)
    location = db.Column(db.String(200), nullable=False)
    image_filename = db.Column(db.String(500), nullable=False)
    ai_description = db.Column(db.Text, nullable=True)
    contact_info = db.Column(db.String(200), nullable=False) # WARNING: Store securely in real app
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def __init__(self, status=None, item_type=None, location=None, image_filename=None, contact_info=None, color=None, brand=None, ai_description=None, user_id=None, reference_id=None, timestamp=None, **kwargs):
        super().__init__(**kwargs)
        self.status = status
        self.item_type = item_type
        self.location = location
        self.image_filename = image_filename
        self.contact_info = contact_info
        self.color = color
        self.brand = brand
        self.ai_description = ai_description
        self.user_id = user_id
        self.reference_id = reference_id or generate_item_reference_id(status)
        if timestamp is not None:
            self.timestamp = timestamp

    def __repr__(self):
        return f'<Item {self.id} - {self.status} - {self.item_type}>'

    # Helper for metadata comparison
    def get_metadata(self):
        return {
            "item_type": self.item_type.lower() if self.item_type else "",
            "color": self.color.lower() if self.color else "",
            "brand": self.brand.lower() if self.brand else "",
            "location": self.location.lower() if self.location else "" # Simple comparison
        }


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    avatar_url = db.Column(db.String(500), nullable=True)
    phone = db.Column(db.String(30), nullable=True)
    address = db.Column(db.String(300), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    alternate_contact = db.Column(db.String(150), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def is_profile_complete(self):
        return bool(self.phone and self.address)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "avatar_url": self.avatar_url,
            "phone": self.phone,
            "address": self.address,
            "city": self.city,
            "alternate_contact": self.alternate_contact,
            "profile_complete": self.is_profile_complete,
        }


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    found_item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=True)
    lost_item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    confidence = db.Column(db.Float, nullable=True)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self, found_item=None):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "found_item_id": self.found_item_id,
            "lost_item_id": self.lost_item_id,
            "title": self.title,
            "message": self.message,
            "confidence": self.confidence,
            "is_read": self.is_read,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "found_item": found_item,
        }