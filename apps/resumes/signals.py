from django.db.models.signals import post_delete
from django.dispatch import receiver

from apps.resumes.models import Resume, ResumeWork


@receiver(post_delete, sender=Resume)
def queue_deleted_resume_file(sender, instance, using, **kwargs):
    if instance.file:
        ResumeWork.objects.using(using).get_or_create(
            kind=ResumeWork.Kind.DELETE, target=instance.file.name,
        )
