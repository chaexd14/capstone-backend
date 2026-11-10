import time
import os

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.resumes.models import WorkerHeartbeat
from apps.resumes.tasks import run_next_resume_work
from config.throttling import purge_expired_throttle_windows


class Command(BaseCommand):
    help = "Process queued resume scans, evaluations, and file deletions."

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Drain currently ready work and exit.')

    def handle(self, *args, **options):
        next_purge = 0
        next_heartbeat = 0
        while True:
            close_old_connections()
            if time.monotonic() >= next_heartbeat:
                WorkerHeartbeat.objects.update_or_create(
                    name='resume-worker',
                    defaults={'metadata': {'pid': os.getpid()}},
                )
                next_heartbeat = time.monotonic() + 15
            if time.monotonic() >= next_purge:
                purge_expired_throttle_windows()
                next_purge = time.monotonic() + 60
            if not run_next_resume_work():
                if options['once']:
                    return
                time.sleep(2)
