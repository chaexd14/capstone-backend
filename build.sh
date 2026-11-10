#!/usr/bin/env bash
# Exit immediately if a command exits with a non-zero status
set -o errexit

echo "==> Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Checking deployment settings and restricted database access..."
python manage.py check --deploy --fail-level ERROR
python manage.py migrate --check
python manage.py check_runtime_database

echo "==> Collecting static files..."
python manage.py collectstatic --no-input

echo "==> Build completed successfully!"
