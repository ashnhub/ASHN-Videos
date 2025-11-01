from home import app, db, User  # ⚠️ on réutilise app et db existants

email = "tonemail@example.com"  # <-- Mets ici ton email d'utilisateur déjà inscrit

with app.app_context():
    user = User.query.filter_by(email=email).first()
    if user:
        user.is_admin = True
        db.session.commit()
        print(f"✅ Utilisateur {email} est maintenant admin.")
    else:
        print(f"❌ Utilisateur {email} introuvable.")
