from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(10), nullable=False) # 'lost' or 'found'
    item_type = db.Column(db.String(100), nullable=False)
    color = db.Column(db.String(50), nullable=True)
    brand = db.Column(db.String(100), nullable=True)
    location = db.Column(db.String(200), nullable=False)
    image_filename = db.Column(db.String(500), nullable=False)
    ai_description = db.Column(db.Text, nullable=True)
    contact_info = db.Column(db.String(200), nullable=False) # WARNING: Store securely in real app
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def __init__(self, status=None, item_type=None, location=None, image_filename=None, contact_info=None, color=None, brand=None, ai_description=None, timestamp=None, **kwargs):
        super().__init__(**kwargs)
        self.status = status
        self.item_type = item_type
        self.location = location
        self.image_filename = image_filename
        self.contact_info = contact_info
        self.color = color
        self.brand = brand
        self.ai_description = ai_description
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