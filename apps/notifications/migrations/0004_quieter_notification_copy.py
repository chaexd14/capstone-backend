import re

from django.db import migrations


STATUS_FROM_MESSAGE = re.compile(r"has been moved to '([^']+)'\.?$")
STATUS_FROM_TITLE = re.compile(r"^Application Update:\s*(.+)$")
CANDIDATE_FROM_MESSAGE = re.compile(r"^Candidate\s+([^\s]+)\s+applied for\s+(.+)\.$")


def _related_values(notification):
    application = notification.application
    job = notification.job or (application.job if application else None)
    # Migration-state models do not expose properties from the runtime model.
    candidate_code = f'TM-{application.pk:04d}' if application else None
    job_title = job.title if job else None
    return candidate_code, job_title


def _status_label(notification):
    message_match = STATUS_FROM_MESSAGE.search(notification.message or '')
    if message_match:
        return message_match.group(1).strip()
    title_match = STATUS_FROM_TITLE.match(notification.title or '')
    if title_match:
        return title_match.group(1).strip()
    return None


def simplify_notification_copy(apps, schema_editor):
    Notification = apps.get_model('notifications', 'Notification')
    queryset = Notification.objects.select_related('application__job', 'job').all()

    for notification in queryset.iterator(chunk_size=500):
        candidate_code, job_title = _related_values(notification)
        notification_type = notification.notification_type
        next_title = notification.title
        next_message = notification.message

        if notification_type == 'NEW_APPLICATION':
            next_title = 'New Application'
            if candidate_code and job_title:
                next_message = f'Candidate {candidate_code} applied for {job_title}.'
        elif notification_type == 'APPLICATION_SUBMITTED':
            next_title = 'Application Submitted'
            if job_title:
                next_message = f'Your application for {job_title} was submitted.'
        elif notification_type == 'STATUS_UPDATE':
            next_title = 'Application Update'
            status_label = _status_label(notification)
            if job_title and status_label:
                next_message = (
                    f"Your application for {job_title} has been moved to '{status_label}'."
                )

        changed_fields = []
        if notification.title != next_title:
            notification.title = next_title
            changed_fields.append('title')
        if notification.message != next_message:
            notification.message = next_message
            changed_fields.append('message')
        if changed_fields:
            notification.save(update_fields=changed_fields)


def restore_notification_copy(apps, schema_editor):
    Notification = apps.get_model('notifications', 'Notification')
    queryset = Notification.objects.select_related('application__job', 'job').all()

    for notification in queryset.iterator(chunk_size=500):
        candidate_code, job_title = _related_values(notification)
        notification_type = notification.notification_type
        next_title = notification.title
        next_message = notification.message

        if notification_type == 'NEW_APPLICATION':
            if not candidate_code or not job_title:
                match = CANDIDATE_FROM_MESSAGE.match(notification.message or '')
                if match:
                    candidate_code = candidate_code or match.group(1)
                    job_title = job_title or match.group(2)
            if candidate_code:
                next_title = f'New Candidate Applied: {candidate_code}'
            if candidate_code and job_title:
                next_message = f'Candidate {candidate_code} applied for {job_title}.'
        elif notification_type == 'APPLICATION_SUBMITTED':
            next_title = 'Application Submitted'
            if candidate_code and job_title:
                next_message = (
                    f'Your application for {job_title} ({candidate_code}) '
                    'was successfully submitted.'
                )
        elif notification_type == 'STATUS_UPDATE':
            status_label = _status_label(notification)
            if status_label:
                next_title = f'Application Update: {status_label}'
            if candidate_code and job_title and status_label:
                next_message = (
                    f'Your application for {job_title} ({candidate_code}) '
                    f"has been moved to '{status_label}'."
                )

        changed_fields = []
        if notification.title != next_title:
            notification.title = next_title
            changed_fields.append('title')
        if notification.message != next_message:
            notification.message = next_message
            changed_fields.append('message')
        if changed_fields:
            notification.save(update_fields=changed_fields)


class Migration(migrations.Migration):
    dependencies = [
        ('notifications', '0003_realtime_and_query_indexes'),
    ]

    operations = [
        migrations.RunPython(simplify_notification_copy, restore_notification_copy),
    ]
