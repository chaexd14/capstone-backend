import logging
from django.db.models.signals import post_delete
from django.dispatch import receiver
from apps.resumes.models import Resume

logger = logging.getLogger(__name__)

@receiver(post_delete, sender=Resume)
def auto_delete_resume_file_on_delete(sender, instance, **kwargs):
    """
    Deletes resume file from Cloudflare R2 storage when the Resume record is deleted.
    Triggers automatically when:
    - A resume is deleted directly
    - An application is deleted (cascades to resume)
    - A job is deleted (cascades to applications -> resumes)
    """
    if instance.file:
        try:
            instance.file.delete(save=False)
            logger.info(f"[R2 STORAGE] Deleted resume file {instance.file.name} for candidate {instance.original_filename}")
        except Exception as e:
            logger.error(f"[R2 STORAGE] Error deleting resume file {instance.file.name}: {e}")
