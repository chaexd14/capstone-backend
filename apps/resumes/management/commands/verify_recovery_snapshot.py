from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from apps.applications.models import Application


class Command(BaseCommand):
    help = 'Verify that a restored application and its resume object are internally consistent.'

    def add_arguments(self, parser):
        parser.add_argument('--application-id', type=int, required=True)

    def handle(self, *args, **options):
        application = (
            Application.objects.select_related('job', 'job__company', 'resume')
            .filter(pk=options['application_id'])
            .first()
        )
        if application is None:
            raise CommandError('Restored application was not found.')
        resume = getattr(application, 'resume', None)
        if resume is None or not resume.file or not resume.file.name:
            raise CommandError('Restored application has no resume object reference.')
        if not default_storage.exists(resume.file.name):
            raise CommandError('Restored resume object is missing from configured storage.')
        if application.job.company_id is None:
            raise CommandError('Restored application job is not assigned to a company.')

        self.stdout.write(self.style.SUCCESS(
            'Recovery snapshot is consistent: application, company, resume row, and storage object exist.'
        ))
