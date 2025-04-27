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
    image_filename = db.Column(db.String(200), nullable=False)
    ai_description = db.Column(db.Text, nullable=True)
    contact_info = db.Column(db.String(200), nullable=False) # WARNING: Store securely in real app
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

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