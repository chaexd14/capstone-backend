import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.resumes.tasks import run_next_resume_work


class Command(BaseCommand):
    help = "Process queued resume scans, evaluations, and file deletions."

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Drain currently ready work and exit.')

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            if not run_next_resume_work():
                if options['once']:
                    return
                time.sleep(2)
