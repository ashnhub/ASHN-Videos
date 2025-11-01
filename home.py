import os
from flask import Flask, request, render_template_string, url_for, redirect, send_from_directory, abort, jsonify, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import subprocess
import shutil
from PIL import Image

# ------------------------------
# Configuration de l'application Flask
# ------------------------------

app = Flask(__name__)

# Configuration
app.config.update(
    SQLALCHEMY_DATABASE_URI="sqlite:///ashn.db",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SECRET_KEY="dev-ashn-secret-key-change-in-production",
    MAX_CONTENT_LENGTH=1024 * 1024 * 1024,  # 1 Go max pour les fichiers uploadés
    DEBUG=True,
)

# Initialisation de la base de données
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Initialisation de Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# -------------------------
# Répertoires
# -------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
HLS_DIR = os.path.join(UPLOAD_DIR, "hls")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(HLS_DIR, exist_ok=True)

# -------------------------
# Modèles
# -------------------------
class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, index=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_admin = db.Column(db.Boolean, default=False)

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw) -> bool:
        return check_password_hash(self.password_hash, raw)


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
        if self.hls_manifest:
            return url_for("hls", filename=self.hls_manifest, _external=False)
        if self.filename:
            return url_for("media", filename=self.filename, _external=False)
        return self.external_url or ""


class Comment(db.Model):
    __tablename__ = "comments"
    id = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.Integer, db.ForeignKey("videos.id"), index=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True, nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='comments', lazy=True)
    video = db.relationship('Video', backref='comments', lazy=True)


class Like(db.Model):
    __tablename__ = "likes"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    video_id = db.Column(db.Integer, db.ForeignKey("videos.id"), nullable=False)
    is_like = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("user_id", "video_id", name="unique_user_video_like"),)


class Follow(db.Model):
    __tablename__ = "follows"
    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    followed_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("follower_id", "followed_id", name="unique_follow"),)


# -------------------------
# Données constantes
# -------------------------
CATEGORIES = [
    {"id": "tendance", "label": "Tendances"},
    {"id": "jeux", "label": "Jeux"},
    {"id": "musique", "label": "Musique"},
    {"id": "film", "label": "Films & Anim"},
]
CATEGORIES_MAP = {c["id"]: c for c in CATEGORIES}
ALLOWED_EXTENSIONS = {"mp4", "webm", "ogg", "mov", "m4v"}

# -------------------------
# Login manager
# -------------------------
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# -------------------------
# Utils
# -------------------------
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def ffmpeg_exists() -> bool:
    return shutil.which("ffmpeg") is not None

def transcode_to_hls(input_path: str, target_dir: str) -> str:
    """Transcode en HLS multi-qualité (360p, 720p). Retourne chemin relatif du master.m3u8."""
    os.makedirs(target_dir, exist_ok=True)
    master_path = os.path.join(target_dir, "master.m3u8")
    
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-filter:v:0", "scale=w=640:h=360:force_original_aspect_ratio=decrease",
        "-c:a:0", "aac", "-ar:0", "48000", "-c:v:0", "h264", "-profile:v:0", "main",
        "-crf:0", "23", "-sc_threshold", "0", "-g", "48", "-keyint_min", "48",
        "-filter:v:1", "scale=w=1280:h=720:force_original_aspect_ratio=decrease",
        "-c:a:1", "aac", "-ar:1", "48000", "-c:v:1", "h264", "-profile:v:1", "main",
        "-crf:1", "21", "-sc_threshold", "0", "-g", "48", "-keyint_min", "48",
        "-map", "0:v:0", "-map", "0:a:0?", "-map", "0:v:0", "-map", "0:a:0?",
        "-var_stream_map", "v:0,a:0 v:1,a:1", "-master_pl_name", "master.m3u8",
        "-f", "hls", "-hls_time", "4", "-hls_playlist_type", "vod",
        "-hls_segment_filename", os.path.join(target_dir, "v%v/seg_%03d.ts"),
        os.path.join(target_dir, "v%v/index.m3u8"),
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        print("FFmpeg error:", e.stderr.decode(errors="ignore")[:2000])
        raise

    if not os.path.exists(master_path):
        with open(master_path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n#EXT-X-VERSION:3\n")
            f.write("#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\nv0/index.m3u8\n")
            f.write("#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720\nv1/index.m3u8\n")

    rel = os.path.relpath(master_path, HLS_DIR)
    return rel.replace("\\", "/")

def init_db():
    """Initialise la base de données avec des données de test"""
    with app.app_context():
        try:
            db.create_all()
            if User.query.count() == 0:
                u = User(email="demo@ashn.dev", display_name="Demo")
                u.set_password("demo1234")
                db.session.add(u)
                db.session.commit()
                print("Utilisateur demo créé: demo@ashn.dev / demo1234")
            
            if Video.query.count() == 0:
                user = User.query.first()
                if user:
                    demo = Video(
                        title="Big Buck Bunny — Démo",
                        description="Vidéo de démonstration pour ASHN Vidéos.",
                        category="film",
                        external_url="https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4",
                        thumb_url="https://picsum.photos/seed/ashn-demo/640/360",
                        duration="10:34",
                        creator="ASHN",
                        user_id=user.id,
                    )
                    db.session.add(demo)
                    db.session.commit()
                    print("Vidéo de démo créée")
        except Exception as e:
            print(f"Erreur lors de l'initialisation de la DB: {e}")

# -------------------------
# Templates avec thème noir
# -------------------------
BASE_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
    <style>
        body {
            background-color: #0f0f0f;
            color: #f1f1f1;
        }
        .bg-dark { background-color: #1a1a1a; }
        .bg-darker { background-color: #0f0f0f; }
        .border-dark { border-color: #303030; }
        .text-gray { color: #aaaaaa; }
        .hover-bg-dark:hover { background-color: #2a2a2a; }
    </style>
</head>
<body class="bg-darker">
    <nav class="bg-dark shadow-lg border-b border-dark sticky top-0 z-50">
        <div class="container mx-auto px-4 py-3 flex items-center justify-between">
            <a href="{{ url_for('home') }}" class="text-2xl font-bold text-red-600 flex items-center">
                <svg class="w-8 h-8 mr-2" fill="currentColor" viewBox="0 0 20 20">
                    <path d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z"/>
                </svg>
                ASHN Vidéos
            </a>
            <div class="flex items-center space-x-4">
                {% if current_user.is_authenticated %}
                    <a href="{{ url_for('upload_form') }}" class="bg-red-600 text-white px-4 py-2 rounded-lg hover:bg-red-700 transition">Upload</a>
                    <span class="text-gray">{{ current_user.display_name }}</span>
                    <a href="{{ url_for('logout') }}" class="text-gray hover:text-white transition">Déconnexion</a>
                {% else %}
                    <a href="{{ url_for('login') }}" class="text-red-500 hover:text-red-400 transition">Connexion</a>
                    <a href="{{ url_for('register') }}" class="bg-red-600 text-white px-4 py-2 rounded-lg hover:bg-red-700 transition">Inscription</a>
                {% endif %}
            </div>
        </div>
    </nav>
    
    {% with messages = get_flashed_messages() %}
        {% if messages %}
            <div class="container mx-auto px-4 py-2">
                {% for message in messages %}
                    <div class="bg-red-900 border border-red-700 text-red-200 px-4 py-3 rounded mb-4">{{ message }}</div>
                {% endfor %}
            </div>
        {% endif %}
    {% endwith %}
    
    {{ body|safe }}
    
    <footer class="bg-dark text-gray text-center py-6 mt-12 border-t border-dark">
        <p>&copy; {{ year }} ASHN Vidéos</p>
    </footer>
</body>
</html>"""

HOME_BODY = """
<main class="container mx-auto px-4 py-8">
    <div class="mb-6">
        <form method="get" class="flex gap-4 mb-4">
            <input name="q" value="{{ q }}" placeholder="Rechercher..." 
                   class="flex-1 px-4 py-2 bg-dark border border-dark rounded-lg text-white focus:border-red-600 focus:outline-none">
            <input name="cat" value="{{ active_cat }}" type="hidden">
            <button type="submit" class="bg-red-600 text-white px-6 py-2 rounded-lg hover:bg-red-700 transition">Rechercher</button>
        </form>
        
        <div class="flex gap-2 overflow-x-auto pb-2">
            {% for cat in categories %}
                <a href="?cat={{ cat.id }}&q={{ q }}" 
                   class="px-4 py-2 rounded-lg whitespace-nowrap {% if cat.id == active_cat %}bg-red-600 text-white{% else %}bg-dark text-gray hover:bg-gray-800{% endif %} transition">
                    {{ cat.label }}
                </a>
            {% endfor %}
        </div>
    </div>
    
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {% for video in items %}
            <div class="bg-dark rounded-lg overflow-hidden hover:bg-gray-900 transition cursor-pointer">
                <a href="{{ url_for('watch', video_id=video.id) }}">
                    {% if video.thumb_url %}
                        <img src="{{ video.thumb_url }}" alt="{{ video.title }}" class="w-full h-48 object-cover">
                    {% else %}
                        <div class="w-full h-48 bg-gray-800 flex items-center justify-center">
                            <svg class="w-16 h-16 text-gray-600" fill="currentColor" viewBox="0 0 20 20">
                                <path d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z"/>
                            </svg>
                        </div>
                    {% endif %}
                </a>
                <div class="p-4">
                    <h3 class="font-semibold mb-2 text-white">
                        <a href="{{ url_for('watch', video_id=video.id) }}" class="hover:text-red-500 transition">
                            {{ video.title }}
                        </a>
                    </h3>
                    <p class="text-gray text-sm">{{ video.creator }}</p>
                    <p class="text-gray text-sm">{{ video.views or 0 }} vues</p>
                </div>
            </div>
        {% else %}
            <div class="col-span-full text-center py-12">
                <svg class="w-24 h-24 mx-auto text-gray-700 mb-4" fill="currentColor" viewBox="0 0 20 20">
                    <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/>
                </svg>
                <p class="text-gray text-lg">Aucune vidéo trouvée.</p>
            </div>
        {% endfor %}
    </div>
</main>
"""

WATCH_BODY = """
<main class="container mx-auto px-4 py-8">
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div class="lg:col-span-2">
            <div class="bg-black rounded-lg overflow-hidden mb-4">
                <video id="video-player" controls class="w-full h-auto" style="max-height: 600px;">
                    <source src="{{ video.source_url }}" type="video/mp4">
                    Votre navigateur ne supporte pas la lecture vidéo.
                </video>
            </div>
            
            <h1 class="text-2xl font-bold mb-3 text-white">{{ video.title }}</h1>
            <div class="flex items-center justify-between mb-4 bg-dark p-4 rounded-lg">
                <div>
                    <p class="text-white font-semibold">{{ video.creator }}</p>
                    <p class="text-gray text-sm">{{ video.views }} vues • {{ video.created_at.strftime('%d %b %Y') }}</p>
                </div>
                
                {% if current_user.is_authenticated %}
                    <div class="flex items-center space-x-2">
                        <button onclick="likeVideo({{ video.id }})" 
                                class="flex items-center space-x-2 px-4 py-2 rounded-full bg-gray-800 hover:bg-gray-700 transition">
                            <span class="text-xl">👍</span>
                            <span id="likes-count" class="text-white">{{ video.likes or 0 }}</span>
                        </button>
                        <button onclick="dislikeVideo({{ video.id }})" 
                                class="flex items-center space-x-2 px-4 py-2 rounded-full bg-gray-800 hover:bg-gray-700 transition">
                            <span class="text-xl">👎</span>
                            <span id="dislikes-count" class="text-white">{{ video.dislikes or 0 }}</span>
                        </button>
                    </div>
                {% endif %}
            </div>
            
            <div class="bg-dark p-4 rounded-lg mb-6">
                <p class="text-gray">{{ video.description or "Aucune description" }}</p>
            </div>
            
            <!-- Commentaires -->
            <div class="mb-6">
                <h3 class="text-xl font-semibold mb-4 text-white">{{ comments|length }} commentaire{% if comments|length > 1 %}s{% endif %}</h3>
                
                {% if current_user.is_authenticated %}
                    <form method="post" action="{{ url_for('comment_post', video_id=video.id) }}" class="mb-6">
                        <textarea name="body" placeholder="Ajouter un commentaire..." 
                                  class="w-full p-3 bg-dark border border-dark rounded-lg mb-2 text-white focus:border-red-600 focus:outline-none" rows="3" required></textarea>
                        <button type="submit" class="bg-red-600 text-white px-6 py-2 rounded-lg hover:bg-red-700 transition">Commenter</button>
                    </form>
                {% endif %}
                
                <div class="space-y-4">
                    {% for comment in comments %}
                        <div class="bg-dark p-4 rounded-lg">
                            <div class="flex items-center space-x-2 mb-2">
                                <strong class="text-white">{{ comment.user.display_name }}</strong>
                                <span class="text-gray text-sm">{{ comment.created_at.strftime('%d %b %Y à %H:%M') }}</span>
                            </div>
                            <p class="text-gray">{{ comment.body }}</p>
                        </div>
                    {% else %}
                        <p class="text-gray text-center py-4">Aucun commentaire pour le moment.</p>
                    {% endfor %}
                </div>
            </div>
        </div>
        
        <!-- Suggestions -->
        <div class="space-y-4">
            <h3 class="font-semibold text-lg text-white mb-4">Suggestions</h3>
            {% for suggestion in more %}
                <div class="bg-dark rounded-lg overflow-hidden hover:bg-gray-900 transition cursor-pointer">
                    <a href="{{ url_for('watch', video_id=suggestion.id) }}">
                        {% if suggestion.thumb_url %}
                            <img src="{{ suggestion.thumb_url }}" alt="{{ suggestion.title }}" 
                                 class="w-full h-32 object-cover">
                        {% else %}
                            <div class="w-full h-32 bg-gray-800 flex items-center justify-center">
                                <svg class="w-12 h-12 text-gray-600" fill="currentColor" viewBox="0 0 20 20">
                                    <path d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z"/>
                                </svg>
                            </div>
                        {% endif %}
                    </a>
                    <div class="p-3">
                        <h4 class="font-medium text-sm mb-1 text-white">
                            <a href="{{ url_for('watch', video_id=suggestion.id) }}" class="hover:text-red-500 transition">{{ suggestion.title }}</a>
                        </h4>
                        <p class="text-gray text-xs">{{ suggestion.creator }}</p>
                        <p class="text-gray text-xs">{{ suggestion.views or 0 }} vues</p>
                    </div>
                </div>
            {% else %}
                <p class="text-gray text-sm text-center py-4">Aucune suggestion disponible.</p>
            {% endfor %}
        </div>
    </div>
</main>

<script>
function likeVideo(videoId) {
    fetch(`/video/like/${videoId}`, {method: 'POST'})
        .then(r => r.json())
        .then(data => {
            document.getElementById('likes-count').textContent = data.likes;
            document.getElementById('dislikes-count').textContent = data.dislikes;
        })
        .catch(err => console.error('Erreur like:', err));
}

function dislikeVideo(videoId) {
    fetch(`/video/dislike/${videoId}`, {method: 'POST'})
        .then(r => r.json())
        .then(data => {
            document.getElementById('likes-count').textContent = data.likes;
            document.getElementById('dislikes-count').textContent = data.dislikes;
        })
        .catch(err => console.error('Erreur dislike:', err));
}

// HLS support
const video = document.getElementById('video-player');
const videoSrc = '{{ video.source_url }}';
if (Hls.isSupported() && videoSrc.includes('.m3u8')) {
    const hls = new Hls();
    hls.loadSource(videoSrc);
    hls.attachMedia(video);
    hls.on(Hls.Events.ERROR, function (event, data) {
        console.error('HLS error:', data);
    });
}
</script>
"""

UPLOAD_BODY = """
<main class="container mx-auto px-4 py-8">
    <h1 class="text-3xl font-bold mb-6 text-white">Téléverser une vidéo</h1>
    
    <form method="post" enctype="multipart/form-data" class="max-w-2xl">
        <div class="space-y-4">
            <div>
                <label class="block text-sm font-medium mb-2 text-white">Fichier vidéo</label>
                <input name="file" type="file" accept="video/*" required 
                       class="w-full px-4 py-3 bg-dark border border-dark rounded-lg text-white focus:border-red-600 focus:outline-none">
            </div>
            
            <div>
                <label class="block text-sm font-medium mb-2 text-white">Titre</label>
                <input name="title" type="text" required 
                       class="w-full px-4 py-3 bg-dark border border-dark rounded-lg text-white focus:border-red-600 focus:outline-none">
            </div>
            
            <div>
                <label class="block text-sm font-medium mb-2 text-white">Description</label>
                <textarea name="description" rows="4" 
                          class="w-full px-4 py-3 bg-dark border border-dark rounded-lg text-white focus:border-red-600 focus:outline-none"></textarea>
            </div>
            
            <div>
                <label class="block text-sm font-medium mb-2 text-white">Catégorie</label>
                <select name="category" class="w-full px-4 py-3 bg-dark border border-dark rounded-lg text-white focus:border-red-600 focus:outline-none">
                    {% for cat in categories %}
                        <option value="{{ cat.id }}">{{ cat.label }}</option>
                    {% endfor %}
                </select>
            </div>
            
            <div>
                <label class="block text-sm font-medium mb-2 text-white">Créateur</label>
                <input name="creator" type="text" value="{{ current_user.display_name }}" 
                       class="w-full px-4 py-3 bg-dark border border-dark rounded-lg text-white focus:border-red-600 focus:outline-none">
            </div>
            
            <div class="bg-dark p-4 rounded-lg">
                <label class="flex items-center space-x-3 cursor-pointer">
                    <input name="to_hls" type="checkbox" class="w-5 h-5 text-red-600 bg-gray-800 border-gray-600 rounded focus:ring-red-600">
                    <span class="text-white">Convertir en HLS (streaming adaptatif)</span>
                </label>
            </div>
            
            <button type="submit" class="w-full bg-red-600 text-white px-6 py-3 rounded-lg hover:bg-red-700 transition font-semibold">
                Téléverser la vidéo
            </button>
        </div>
    </form>
</main>
"""

AUTH_BODY = """
<main class="container mx-auto px-4 py-8 max-w-md">
    <h1 class="text-3xl font-bold text-center mb-8 text-white">{{ heading }}</h1>
    
    <form method="post" class="space-y-4 bg-dark p-8 rounded-lg">
        {% if mode == 'register' %}
            <div>
                <label class="block text-sm font-medium mb-2 text-white">Nom d'affichage</label>
                <input name="display_name" type="text" required 
                       class="w-full px-4 py-3 bg-darker border border-dark rounded-lg text-white focus:border-red-600 focus:outline-none">
            </div>
        {% endif %}
        
        <div>
            <label class="block text-sm font-medium mb-2 text-white">Email</label>
            <input name="email" type="email" required 
                   class="w-full px-4 py-3 bg-darker border border-dark rounded-lg text-white focus:border-red-600 focus:outline-none">
        </div>
        
        <div>
            <label class="block text-sm font-medium mb-2 text-white">Mot de passe</label>
            <input name="password" type="password" required 
                   class="w-full px-4 py-3 bg-darker border border-dark rounded-lg text-white focus:border-red-600 focus:outline-none">
        </div>
        
        <button type="submit" class="w-full bg-red-600 text-white py-3 rounded-lg hover:bg-red-700 transition font-semibold">
            {{ cta }}
        </button>
    </form>
    
    <div class="text-center mt-6">
        {% if mode == 'login' %}
            <p class="text-gray">Pas de compte ? <a href="{{ url_for('register') }}" class="text-red-500 hover:text-red-400 transition">S'inscrire</a></p>
        {% else %}
            <p class="text-gray">Déjà un compte ? <a href="{{ url_for('login') }}" class="text-red-500 hover:text-red-400 transition">Se connecter</a></p>
        {% endif %}
    </div>
</main>
"""

PROFIL_BODY = """
<main class="container mx-auto px-4 py-8">
    <div class="bg-dark rounded-lg p-6 mb-8">
        <h1 class="text-3xl font-bold mb-4 text-white">Profil de {{ user.display_name }}</h1>
        <div class="flex items-center space-x-6 text-gray">
            <span><strong class="text-white">{{ videos|length }}</strong> vidéos</span>
            <span>Membre depuis {{ user.created_at.strftime('%B %Y') }}</span>
        </div>
    </div>
    
    <h2 class="text-2xl font-semibold mb-6 text-white">Vidéos de {{ user.display_name }}</h2>
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {% for v in videos %}
            <div class="bg-dark rounded-lg overflow-hidden hover:bg-gray-900 transition cursor-pointer">
                <a href="{{ url_for('watch', video_id=v.id) }}">
                    {% if v.thumb_url %}
                        <img src="{{ v.thumb_url }}" alt="{{ v.title }}" class="w-full h-48 object-cover">
                    {% else %}
                        <div class="w-full h-48 bg-gray-800 flex items-center justify-center">
                            <svg class="w-16 h-16 text-gray-600" fill="currentColor" viewBox="0 0 20 20">
                                <path d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z"/>
                            </svg>
                        </div>
                    {% endif %}
                </a>
                <div class="p-4">
                    <h3 class="font-semibold mb-2 text-white">{{ v.title }}</h3>
                    <p class="text-gray text-sm">{{ v.created_at.strftime('%d %b %Y') }}</p>
                    <p class="text-gray text-sm">{{ v.views or 0 }} vues</p>
                </div>
            </div>
        {% else %}
            <div class="col-span-full text-center py-12">
                <svg class="w-24 h-24 mx-auto text-gray-700 mb-4" fill="currentColor" viewBox="0 0 20 20">
                    <path d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z"/>
                </svg>
                <p class="text-gray text-lg">Aucune vidéo publiée.</p>
            </div>
        {% endfor %}
    </div>
</main>
"""

# -------------------------
# Routes principales
# -------------------------
@app.get("/")
def home():
    try:
        q = (request.args.get("q") or "").strip()
        active_cat = request.args.get("cat") or CATEGORIES[0]["id"]

        query = Video.query.filter_by(category=active_cat)
        if q:
            like = f"%{q}%"
            query = query.filter(db.or_(Video.title.ilike(like), Video.creator.ilike(like)))
        items = query.order_by(Video.created_at.desc()).limit(40).all()

        body = render_template_string(
            HOME_BODY,
            q=q,
            active_cat=active_cat,
            items=items,
            categories=CATEGORIES,
            categories_map=CATEGORIES_MAP,
        )
        return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title="ASHN Vidéos — Accueil")
    except Exception as e:
        print(f"Erreur dans home(): {e}")
        return f"Erreur: {e}", 500

@app.get("/watch/<int:video_id>")
def watch(video_id: int):
    try:
        v = Video.query.get_or_404(video_id)
        v.views = (v.views or 0) + 1
        db.session.commit()

        user_like = None
        is_following = False
        if current_user.is_authenticated:
            user_like = Like.query.filter_by(user_id=current_user.id, video_id=video_id).first()
            if v.user_id:
                is_following = Follow.query.filter_by(
                    follower_id=current_user.id, followed_id=v.user_id
                ).first() is not None

        more = (
            Video.query.filter(Video.id != v.id, Video.category == v.category)
            .order_by(Video.created_at.desc())
            .limit(8)
            .all()
        )

        comments = (
            Comment.query
            .filter(Comment.video_id == v.id)
            .order_by(Comment.created_at.desc())
            .all()
        )

        body = render_template_string(
            WATCH_BODY,
            video=v,
            more=more,
            comments=comments,
            user_like=user_like,
            is_following=is_following
        )
        return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title=v.title)
    except Exception as e:
        print(f"Erreur dans watch(): {e}")
        return f"Erreur: {e}", 500

# -------------------------
# Upload + HLS
# -------------------------
@app.get("/upload")
@login_required
def upload_form():
    try:
        body = render_template_string(UPLOAD_BODY, categories=CATEGORIES)
        return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title="Téléverser — ASHN Vidéos")
    except Exception as e:
        print(f"Erreur dans upload_form(): {e}")
        return f"Erreur: {e}", 500

@app.post("/upload")
@login_required
def upload_post():
    try:
        f = request.files.get("file")
        title = (request.form.get("title") or "Sans titre").strip()
        description = (request.form.get("description") or "").strip()
        category = request.form.get("category") or "tendance"
        creator = (request.form.get("creator") or current_user.display_name or "Anonyme").strip()
        to_hls = request.form.get("to_hls") is not None

        if not f or f.filename == "":
            flash("Aucun fichier reçu")
            return redirect(url_for("upload_form"))
        if not allowed_file(f.filename):
            flash("Extension non supportée")
            return redirect(url_for("upload_form"))

        filename = secure_filename(f.filename)
        base, ext = os.path.splitext(filename)
        counter = 1
        final = filename
        while os.path.exists(os.path.join(UPLOAD_DIR, final)):
            final = f"{base}-{counter}{ext}"
            counter += 1

        file_path = os.path.join(UPLOAD_DIR, final)
        f.save(file_path)

        v = Video(
            title=title,
            description=description,
            category=category if category in CATEGORIES_MAP else "tendance",
            filename=final,
            thumb_url="https://picsum.photos/seed/ashn-" + base + "/640/360",
            duration="",
            creator=creator,
            user_id=current_user.id,
        )

        if to_hls and ffmpeg_exists():
            target_dir = os.path.join(HLS_DIR, f"video_{datetime.utcnow().timestamp():.0f}")
            try:
                rel_master = transcode_to_hls(file_path, target_dir)
                v.hls_manifest = rel_master
            except Exception as e:
                print(f"Erreur transcodage HLS: {e}")
                flash("Transcodage HLS échoué — lecture MP4 directe utilisée.")

        db.session.add(v)
        db.session.commit()

        return redirect(url_for("watch", video_id=v.id))
    except Exception as e:
        print(f"Erreur dans upload_post(): {e}")
        flash(f"Erreur lors de l'upload: {e}")
        return redirect(url_for("upload_form"))

@app.get("/media/<path:filename>")
def media(filename):
    try:
        return send_from_directory(UPLOAD_DIR, filename, as_attachment=False)
    except Exception as e:
        print(f"Erreur dans media(): {e}")
        abort(404)

@app.get("/hls/<path:filename>")
def hls(filename):
    try:
        return send_from_directory(HLS_DIR, filename, as_attachment=False)
    except Exception as e:
        print(f"Erreur dans hls(): {e}")
        abort(404)

# -------------------------
# Authentification
# -------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    try:
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            u = User.query.filter_by(email=email).first()
            if not u or not u.check_password(password):
                flash("Identifiants invalides")
            else:
                login_user(u)
                return redirect(url_for("home"))
        body = render_template_string(AUTH_BODY, heading="Connexion", cta="Se connecter", mode="login")
        return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title="Connexion — ASHN Vidéos")
    except Exception as e:
        print(f"Erreur dans login(): {e}")
        return f"Erreur: {e}", 500

@app.route("/register", methods=["GET", "POST"])
def register():
    try:
        if request.method == "POST":
            display_name = (request.form.get("display_name") or "").strip()
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            if not display_name or not email or not password:
                flash("Tous les champs sont requis")
            elif User.query.filter_by(email=email).first():
                flash("Cet email est déjà utilisé")
            else:
                u = User(email=email, display_name=display_name)
                u.set_password(password)
                db.session.add(u)
                db.session.commit()
                login_user(u)
                return redirect(url_for("home"))
        body = render_template_string(AUTH_BODY, heading="Créer un compte", cta="S'inscrire", mode="register")
        return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title="Inscription — ASHN Vidéos")
    except Exception as e:
        print(f"Erreur dans register(): {e}")
        return f"Erreur: {e}", 500

@app.get("/logout")
@login_required
def logout():
    try:
        logout_user()
        return redirect(url_for("home"))
    except Exception as e:
        print(f"Erreur dans logout(): {e}")
        return redirect(url_for("home"))

# -------------------------
# Commentaires
# -------------------------
@app.post("/watch/<int:video_id>/comment")
@login_required
def comment_post(video_id: int):
    try:
        v = Video.query.get_or_404(video_id)
        body = (request.form.get("body") or "").strip()
        if not body:
            flash("Commentaire vide")
            return redirect(url_for("watch", video_id=v.id))
        c = Comment(video_id=v.id, user_id=current_user.id, body=body)
        db.session.add(c)
        db.session.commit()
        return redirect(url_for("watch", video_id=v.id))
    except Exception as e:
        print(f"Erreur dans comment_post(): {e}")
        flash("Erreur lors de l'ajout du commentaire")
        return redirect(url_for("watch", video_id=video_id))

# -------------------------
# API minimale
# -------------------------
@app.get("/api/videos")
def api_videos():
    try:
        page = max(int(request.args.get("page", 1)), 1)
        per_page = min(max(int(request.args.get("per_page", 12)), 1), 50)
        q = (request.args.get("q") or "").strip()
        cat = request.args.get("cat") or None

        query = Video.query
        if cat:
            query = query.filter_by(category=cat)
        if q:
            like = f"%{q}%"
            query = query.filter(db.or_(Video.title.ilike(like), Video.creator.ilike(like)))

        total = query.count()
        items = (
            query.order_by(Video.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )
        return jsonify({
            "page": page,
            "per_page": per_page,
            "total": total,
            "items": [
                {
                    "id": v.id,
                    "title": v.title,
                    "creator": v.creator,
                    "category": v.category,
                    "views": v.views,
                    "thumb_url": v.thumb_url,
                    "source_url": v.source_url,
                    "hls": bool(v.hls_manifest),
                    "created_at": v.created_at.isoformat(),
                }
                for v in items
            ],
        })
    except Exception as e:
        print(f"Erreur dans api_videos(): {e}")
        return jsonify({"error": str(e)}), 500

# -------------------------
# Routes pour les likes/dislikes
# -------------------------
@app.route("/video/like/<int:video_id>", methods=["POST"])
@login_required
def like_video(video_id):
    try:
        v = Video.query.get_or_404(video_id)

        existing = Like.query.filter_by(user_id=current_user.id, video_id=v.id).first()
        if existing:
            if existing.is_like:
                db.session.delete(existing)
                v.likes = max((v.likes or 1) - 1, 0)
            else:
                existing.is_like = True
                v.likes = (v.likes or 0) + 1
                v.dislikes = max((v.dislikes or 1) - 1, 0)
        else:
            db.session.add(Like(user_id=current_user.id, video_id=v.id, is_like=True))
            v.likes = (v.likes or 0) + 1

        db.session.commit()
        return jsonify({"likes": v.likes, "dislikes": v.dislikes})
    except Exception as e:
        print(f"Erreur dans like_video(): {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/video/dislike/<int:video_id>", methods=["POST"])
@login_required
def dislike_video(video_id):
    try:
        v = Video.query.get_or_404(video_id)

        existing = Like.query.filter_by(user_id=current_user.id, video_id=v.id).first()
        if existing:
            if not existing.is_like:
                db.session.delete(existing)
                v.dislikes = max((v.dislikes or 1) - 1, 0)
            else:
                existing.is_like = False
                v.dislikes = (v.dislikes or 0) + 1
                v.likes = max((v.likes or 1) - 1, 0)
        else:
            db.session.add(Like(user_id=current_user.id, video_id=v.id, is_like=False))
            v.dislikes = (v.dislikes or 0) + 1

        db.session.commit()
        return jsonify({"likes": v.likes, "dislikes": v.dislikes})
    except Exception as e:
        print(f"Erreur dans dislike_video(): {e}")
        return jsonify({"error": str(e)}), 500

# -------------------------
# Route profil utilisateur
# -------------------------
@app.route("/profil/<username>")
def show_profil(username):
    try:
        user = User.query.filter_by(display_name=username).first_or_404()
        videos = Video.query.filter_by(user_id=user.id).order_by(Video.created_at.desc()).all()

        is_following = False
        if current_user.is_authenticated:
            is_following = Follow.query.filter_by(
                follower_id=current_user.id,
                followed_id=user.id
            ).first() is not None

        body = render_template_string(PROFIL_BODY, user=user, videos=videos, is_following=is_following)
        return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title=f"Profil de {user.display_name}")
    except Exception as e:
        print(f"Erreur dans show_profil(): {e}")
        return f"Erreur: {e}", 500

# -------------------------
# Route pour suivre/ne plus suivre
# -------------------------
@app.route("/follow/<int:user_id>", methods=["POST"])
@login_required
def follow_user(user_id):
    try:
        if user_id == current_user.id:
            return jsonify({"error": "Vous ne pouvez pas vous suivre vous-même"}), 400
        
        target_user = User.query.get_or_404(user_id)
        existing = Follow.query.filter_by(follower_id=current_user.id, followed_id=user_id).first()
        
        if existing:
            db.session.delete(existing)
            following = False
        else:
            db.session.add(Follow(follower_id=current_user.id, followed_id=user_id))
            following = True
        
        db.session.commit()
        return jsonify({"following": following})
    except Exception as e:
        print(f"Erreur dans follow_user(): {e}")
        return jsonify({"error": str(e)}), 500

# -------------------------
# Routes admin
# -------------------------
@app.route("/admin/ban/<int:user_id>")
@login_required
def ban_user(user_id):
    try:
        if not current_user.is_admin:
            flash("Accès refusé")
            return redirect(url_for("home"))
        
        user = User.query.get_or_404(user_id)
        if user.id != current_user.id:
            db.session.delete(user)
            db.session.commit()
            flash(f"Utilisateur {user.display_name} banni")
        
        return redirect(url_for("home"))
    except Exception as e:
        print(f"Erreur dans ban_user(): {e}")
        flash("Erreur lors du bannissement")
        return redirect(url_for("home"))

@app.route("/admin/promote/<int:user_id>")
@login_required
def promote_user(user_id):
    try:
        if not current_user.is_admin:
            flash("Accès refusé")
            return redirect(url_for("home"))
        
        user = User.query.get_or_404(user_id)
        user.is_admin = True
        db.session.commit()
        flash(f"Utilisateur {user.display_name} promu admin")
        
        return redirect(url_for("home"))
    except Exception as e:
        print(f"Erreur dans promote_user(): {e}")
        flash("Erreur lors de la promotion")
        return redirect(url_for("home"))

# -------------------------
# Route favicon
# -------------------------
@app.route('/favicon.ico')
def favicon():
    try:
        favicon_path = os.path.join(BASE_DIR, "favicon.ico")
        if not os.path.exists(favicon_path):
            img = Image.new('RGB', (32, 32), color='#dc2626')
            img.save(favicon_path, format="ICO")
        return send_file(favicon_path, mimetype='image/x-icon')
    except Exception:
        return '', 204

# -------------------------
# Gestion d'erreurs
# -------------------------
@app.errorhandler(404)
def not_found_error(error):
    body = "<main class='container mx-auto px-4 py-8 text-center'><h1 class='text-3xl font-bold text-white mb-4'>Page non trouvée</h1><p class='text-gray mb-6'>La page que vous recherchez n'existe pas.</p><p><a href='" + url_for('home') + "' class='bg-red-600 text-white px-6 py-3 rounded-lg hover:bg-red-700 transition inline-block'>Retour à l'accueil</a></p></main>"
    return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title="Erreur 404"), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    body = "<main class='container mx-auto px-4 py-8 text-center'><h1 class='text-3xl font-bold text-white mb-4'>Erreur interne</h1><p class='text-gray mb-6'>Une erreur s'est produite sur le serveur.</p><p><a href='" + url_for('home') + "' class='bg-red-600 text-white px-6 py-3 rounded-lg hover:bg-red-700 transition inline-block'>Retour à l'accueil</a></p></main>"
    return render_template_string(BASE_HTML, body=body, year=datetime.utcnow().year, title="Erreur 500"), 500

# -------------------------
# Commande CLI pour initialiser la DB
# -------------------------
@app.cli.command()
def init_database():
    """Initialise la base de données"""
    init_db()

# -------------------------
# Entrée app
# -------------------------
if __name__ == "__main__":
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
