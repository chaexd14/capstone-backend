from apps.notifications.models import Notification, RecipientType, NotificationType
from apps.applications.models import ApplicationStatus


STATUS_DISPLAY_MAP = {
    ApplicationStatus.SUBMITTED: 'Submitted',
    ApplicationStatus.PROCESSING: 'Processing',
    ApplicationStatus.UNDER_REVIEW: 'Under Review',
    ApplicationStatus.SHORTLISTED: 'Shortlisted',
    ApplicationStatus.INTERVIEW: 'Invited for Interview',
    ApplicationStatus.REJECTED: 'Application Not Selected',
    ApplicationStatus.HIRED: 'Hired / Offer Extended',
}

def create_new_application_notification(application):
    """
    Called when a new application is submitted.
    1. Notifies all recruiters of the new candidate.
    2. Notifies the applicant that their application was received (if user_id present).
    """
    job = application.job
    # 1. Recruiter Notification (blind-screening safe: uses candidate_code, never applicant PII)
    Notification.objects.create(
        recipient_type=RecipientType.RECRUITER,
        application=application,
        job=job,
        title=f"New Candidate Applied: {application.candidate_code}",
        message=f"Candidate {application.candidate_code} applied for {job.title}.",
        notification_type=NotificationType.NEW_APPLICATION,
    )

    # 2. Applicant Notification (if user_id linked)
    if application.user_id:
        Notification.objects.create(
            recipient_type=RecipientType.APPLICANT,
            user_id=application.user_id,
            application=application,
            job=job,
            title="Application Submitted",
            message=f"Your application for {job.title} ({application.candidate_code}) was successfully submitted.",
            notification_type=NotificationType.APPLICATION_SUBMITTED,
        )

def build_status_change_notification(application, previous_status, new_status):
    """
    Builds an unsaved Notification instance for bulk creation when a candidate's
    application status changes, or returns None if no notification is needed.
    """
    if previous_status == new_status or not application.user_id:
        return None

    job = application.job
    status_label = STATUS_DISPLAY_MAP.get(new_status, new_status)

    return Notification(
        recipient_type=RecipientType.APPLICANT,
        user_id=application.user_id,
        application=application,
        job=job,
        title=f"Application Update: {status_label}",
        message=f"Your application for {job.title} ({application.candidate_code}) has been moved to '{status_label}'.",
        notification_type=NotificationType.STATUS_UPDATE,
    )


def create_status_change_notification(application, previous_status, new_status):
    """
    Called when recruiter changes the candidate's application status.
    Notifies the applicant about their updated status.
    """
    notification = build_status_change_notification(application, previous_status, new_status)
    if notification is not None:
        notification.save()
