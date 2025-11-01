# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from extensions import db  # Assure-toi que db = SQLAlchemy() est défini dans extensions.py

# -----------------------------
# User
# -----------------------------
class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, index=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw) -> bool:
        return check_password_hash(self.password_hash, raw)

# -----------------------------
# Video
# -----------------------------
class Video(db.Model):
    __tablename__ = "videos"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    category = db.Column(db.String(40), default="tendance", index=True)
    filename = db.Column(db.String(255), nullable=True)
    external_url = db.Column(db.String(500), nullable=True)
    thumb_url = db.Column(db.String(500), nullable=True)
    duration = db.Column(db.String(20), default="")
    creator = db.Column(db.String(80), default="Anonyme")
    views = db.Column(db.Integer, default=0)
    likes = db.Column(db.Integer, default=0)
    dislikes = db.Column(db.Integer, default=0)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    hls_manifest = db.Column(db.String(500), nullable=True)

    @property
    def source_url(self):
        from flask import url_for
        if self.hls_manifest:
            return url_for("hls", filename=self.hls_manifest, _external=False)
        if self.filename:
            return url_for("media", filename=self.filename, _external=False)
        return self.external_url or ""

# -----------------------------
# Comment
# -----------------------------
class Comment(db.Model):
    __tablename__ = "comments"
    id = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.Integer, db.ForeignKey("videos.id"), index=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True, nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# -----------------------------
# Like
# -----------------------------
class Like(db.Model):
    __tablename__ = "likes"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    video_id = db.Column(db.Integer, db.ForeignKey("videos.id"), nullable=False)
    is_like = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("user_id", "video_id", name="unique_user_video_like"),)

# -----------------------------
# Follow
# -----------------------------
class Follow(db.Model):
    __tablename__ = "follows"
    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    followed_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("follower_id", "followed_id", name="unique_follow"),)
