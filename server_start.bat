@echo off
echo === Lancement du serveur Flask + Ngrok ===

:: Activer l'environnement virtuel
call .venv\Scripts\activate

:: Lancer Flask en arrière-plan (port 8000)
start cmd /k "flask run --host=0.0.0.0 --port=8000"

:: Attendre un peu que Flask démarre
timeout /t 5 >nul

:: Lancer Ngrok (redirige vers Flask)
ngrok http 8000
