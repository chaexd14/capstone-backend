from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.resumes.models import ResumeWork, WorkerHeartbeat


class Command(BaseCommand):
    help = 'Fail when the resume worker heartbeat, queue age, or failed-work count is unhealthy.'

    def add_arguments(self, parser):
        parser.add_argument('--max-heartbeat-age', type=int, default=60)
        parser.add_argument('--max-queue-age', type=int, default=300)
        parser.add_argument('--max-failed', type=int, default=0)

    def handle(self, *args, **options):
        now = timezone.now()
        failures = []
        heartbeat = WorkerHeartbeat.objects.filter(name='resume-worker').first()
        if heartbeat is None:
            failures.append('resume worker has not reported a heartbeat')
        else:
            heartbeat_age = max(0, int((now - heartbeat.updated_at).total_seconds()))
            if heartbeat_age > options['max_heartbeat_age']:
                failures.append(
                    f'worker heartbeat is {heartbeat_age}s old '
                    f'(limit {options["max_heartbeat_age"]}s)'
                )

        oldest_ready = (
            ResumeWork.objects.filter(failed=False, run_after__lte=now)
            .order_by('run_after')
            .values_list('run_after', flat=True)
            .first()
        )
        if oldest_ready:
            queue_age = max(0, int((now - oldest_ready).total_seconds()))
            if queue_age > options['max_queue_age']:
                failures.append(
                    f'oldest ready work is {queue_age}s old '
                    f'(limit {options["max_queue_age"]}s)'
                )

        failed_count = ResumeWork.objects.filter(failed=True).count()
        if failed_count > options['max_failed']:
            failures.append(
                f'{failed_count} failed work item(s) exceed limit {options["max_failed"]}'
            )

        if failures:
            raise CommandError('; '.join(failures))

        self.stdout.write(self.style.SUCCESS('Resume worker heartbeat and queue are healthy.'))
