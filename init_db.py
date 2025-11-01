from home import app, db

with app.app_context():
    db.create_all()
    print("Base de données initialisée !")
