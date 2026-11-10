#!/usr/bin/env bash
set -euo pipefail

python manage.py check --deploy --fail-level ERROR
python manage.py migrate --check
python manage.py check_runtime_database

case "${1:-web}" in
    web)
        python manage.py collectstatic --no-input
        exec gunicorn config.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --workers 2 --timeout 120
        ;;
    worker)
        freshclam --config-file=/app/freshclam-production.conf
        freshclam --config-file=/app/freshclam-production.conf --daemon --foreground &
        updater=$!
        python manage.py process_resume_work &
        worker=$!
        trap 'kill -TERM "$updater" "$worker" 2>/dev/null || true; wait || true' EXIT
        trap 'exit 0' TERM INT
        wait -n "$updater" "$worker"
        exit 1
        ;;
    *)
        echo 'Usage: start-production.sh web|worker' >&2
        exit 2
        ;;
esac
