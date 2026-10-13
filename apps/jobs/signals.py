import logging
from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.jobs.models import Job, JobStatus

logger = logging.getLogger(__name__)

def purge_job_resumes(job):
    """
    Deletes physical resume files from Cloudflare R2 for all applications
    belonging to a closed job, while preserving candidate records, notes, and match scores.
    """
    purged_count = 0
    applications = job.applications.select_related('resume').all()
    for app in applications:
        if hasattr(app, 'resume') and app.resume and app.resume.file:
            resume = app.resume
            file_name = resume.file.name
            try:
                resume.file.delete(save=False)
                logger.info(f"[R2 STORAGE] Purged file {file_name} for candidate {app.candidate_code}")
            except Exception as e:
                logger.error(f"[R2 STORAGE] Failed to purge file {file_name}: {e}")
            resume.file = None
            resume.processing_status = 'PURGED'
            resume.save(update_fields=['file', 'processing_status'])
            purged_count += 1
    return purged_count

@receiver(post_save, sender=Job)
def handle_job_status_change(sender, instance, created, **kwargs):
    """
    Listens for Job changes: when job status is CLOSED, automatically purge
    all attached candidate resumes from Cloudflare R2.
    """
    if instance.status == JobStatus.CLOSED:
        purged = purge_job_resumes(instance)
        if purged > 0:
            logger.info(f"[R2 STORAGE] Job #{instance.id} '{instance.title}' marked CLOSED: Purged {purged} resumes from R2.")
