#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt

# Spécifiez l'application Flask
export FLASK_APP=home.py

# Exécutez les migrations
flask db upgrade
