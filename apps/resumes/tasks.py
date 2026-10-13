import logging
from concurrent.futures import ThreadPoolExecutor
from django.db import close_old_connections

logger = logging.getLogger(__name__)

# Bounded thread pool executor: Limits concurrent AI evaluations to 4 workers.
# This prevents exhausting CPU/memory and avoids tripping Gemini API rate limits
# when dozens of applicants submit resumes simultaneously.
_resume_worker_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="resume_eval_worker")


def _run_resume_processing_task(application_id: str) -> None:
    """
    Worker task executing in a background thread.
    Safely opens and cleans up database connections to avoid leaking connection handles.
    """
    from apps.applications.models import Application
    from apps.resumes.services import process_resume_and_calculate_match
    from django.core.cache import cache

    close_old_connections()
    try:
        application = Application.objects.select_related('job', 'resume').filter(id=application_id).first()
        if not application:
            logger.warning(f"[TASK WORKER] Application {application_id} not found. Skipping.")
            return

        logger.info(f"[TASK WORKER] Starting background evaluation for Application {application.id} ({application.candidate_code}).")
        match_result = process_resume_and_calculate_match(application)
        
        # Clear list cache so dashboards and applicant tables reflect fresh data
        cache.clear()
        logger.info(f"[TASK WORKER] Completed background evaluation for Application {application.id}. Result: {bool(match_result)}")
    except Exception as exc:
        logger.exception(f"[TASK WORKER] Error evaluating Application {application_id}: {exc}")
        try:
            from apps.applications.models import Application
            app = Application.objects.filter(id=application_id).first()
            if app and hasattr(app, 'resume') and app.resume:
                app.resume.processing_status = 'FAILED'
                app.resume.save(update_fields=['processing_status'])
        except Exception:
            pass
    finally:
        close_old_connections()


def enqueue_resume_processing(application_id) -> None:
    """
    Dispatches resume processing to the background worker pool without blocking the HTTP request.
    """
    app_id_str = str(application_id)
    logger.info(f"[TASK QUEUE] Enqueueing resume processing for application: {app_id_str}")
    _resume_worker_pool.submit(_run_resume_processing_task, app_id_str)
