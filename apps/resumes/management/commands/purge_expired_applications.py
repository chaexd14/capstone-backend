from datetime import date, datetime, time

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import SecurityEvent
from apps.jobs.models import Job, JobStatus


class Command(BaseCommand):
    help = "Preview or purge candidate records for closed or archived jobs before a retention cutoff."

    def add_arguments(self, parser):
        parser.add_argument('--before', required=True, help='Exclusive cutoff date (YYYY-MM-DD).')
        parser.add_argument('--apply', action='store_true', help='Delete the records shown by the preview.')
        parser.add_argument('--actor', help='Operator identity for the audit record when using --apply.')

    def handle(self, *args, **options):
        try:
            cutoff = timezone.make_aware(datetime.combine(date.fromisoformat(options['before']), time.min))
        except ValueError as exc:
            raise CommandError("Enter a valid cutoff date in YYYY-MM-DD format.") from exc
        if cutoff >= timezone.now():
            raise CommandError("The retention cutoff must be in the past.")
        actor = options['actor']
        if options['apply'] and (not actor or len(actor) > 150):
            raise CommandError("Provide an operator identifier of 1 to 150 characters with --apply.")

        eligible = Job.objects.filter(
            status__in=[JobStatus.CLOSED, JobStatus.ARCHIVED], updated_at__lt=cutoff,
        )
        total = 0
        for job_id in eligible.values_list('pk', flat=True).iterator():
            with transaction.atomic():
                job = eligible.select_for_update().filter(pk=job_id).first()
                if job is None:
                    continue
                count = job.applications.count()
                if options['apply'] and count:
                    job.applications.all().delete()
                    SecurityEvent.objects.create(
                        actor_id=actor, action='retention.purge', object_id=str(job_id),
                        details={'applications': count, 'cutoff': options['before']},
                    )
                total += count
        verb = 'Purged' if options['apply'] else 'Eligible'
        self.stdout.write(f"{verb} candidate records: {total}")
