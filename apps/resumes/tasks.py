import logging
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import close_old_connections, transaction
from django.utils import timezone

from apps.applications.models import Application
from apps.resumes.models import ProcessingStatus, Resume, ResumeWork
from apps.resumes.upload_validation import validate_resume_format

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5

_resume_worker_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="resume_work")
_payload_cache_lock = threading.Lock()
_payload_cache: dict[int, tuple[float, bytes]] = {}
_PAYLOAD_CACHE_TTL_SECONDS = 120.0
_PAYLOAD_CACHE_MAX_ITEMS = 16


def cache_resume_payload(resume_id: int | None, payload: bytes | None) -> None:
    if not resume_id or not payload:
        return
    now = time.monotonic()
    with _payload_cache_lock:
        expired = [k for k, (exp, _) in _payload_cache.items() if exp <= now]
        for k in expired:
            _payload_cache.pop(k, None)
        while len(_payload_cache) >= _PAYLOAD_CACHE_MAX_ITEMS:
            oldest_key = next(iter(_payload_cache))
            _payload_cache.pop(oldest_key, None)
        _payload_cache[int(resume_id)] = (now + _PAYLOAD_CACHE_TTL_SECONDS, payload)


def get_cached_resume_payload(resume_id: int | None, *, consume: bool = False) -> bytes | None:
    if not resume_id:
        return None
    now = time.monotonic()
    with _payload_cache_lock:
        entry = _payload_cache.pop(int(resume_id), None) if consume else _payload_cache.get(int(resume_id))
        if not entry:
            return None
        expires_at, data = entry
        if expires_at <= now:
            _payload_cache.pop(int(resume_id), None)
            return None
        return data


def _drain_resume_work_in_background() -> None:
    close_old_connections()
    try:
        while run_next_resume_work():
            pass
    except Exception:
        logger.exception("Unexpected error in background resume worker.")
    finally:
        close_old_connections()


def _schedule_background_drain() -> None:
    try:
        _resume_worker_pool.submit(_drain_resume_work_in_background)
    except Exception:
        logger.exception("Failed to submit resume work to background thread pool.")


def enqueue_resume_processing(application_id, *, retry_failed=False):
    work, _ = ResumeWork.objects.get_or_create(
        kind=ResumeWork.Kind.EVALUATE, target=str(application_id)
    )
    if retry_failed and work.failed:
        ResumeWork.objects.filter(pk=work.pk, failed=True).update(
            attempts=0, failed=False, run_after=timezone.now(),
        )
    transaction.on_commit(_schedule_background_drain)


def scan_resume(resume):
    if not settings.REQUIRE_RESUME_SCAN or resume.scan_passed:
        return True
    payload = get_cached_resume_payload(resume.pk, consume=False)
    if payload is None:
        with resume.file.open('rb') as source:
            payload = source.read(settings.MAX_RESUME_UPLOAD_SIZE + 1)
    if len(payload) > settings.MAX_RESUME_UPLOAD_SIZE:
        return False
    try:
        validate_resume_format(BytesIO(payload), resume.original_filename)
    except ValidationError:
        return False
    scan = subprocess.run(
        [settings.RESUME_SCANNER, '--no-summary', '--alert-encrypted=yes',
         '--alert-exceeds-max=yes', '--fail-if-cvd-older-than=2', '-'], input=payload,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=60, check=False,
        env={key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'WINDIR') if key in os.environ},
    )
    if scan.returncode == 1:
        return False
    if scan.returncode != 0:
        raise RuntimeError("Resume scanner failed")
    resume.scan_passed = True
    resume.save(update_fields=['scan_passed'])
    return True


def evaluate_resume(application_id, *, scan_only=False):
    from apps.resumes.services import process_resume_and_calculate_match

    application = Application.objects.select_related('job', 'resume').filter(pk=application_id).first()
    if application is None:
        return
    resume = getattr(application, 'resume', None) or Resume.objects.filter(application=application).first()
    if not resume or not resume.file:
        return
    if not scan_resume(resume):
        resume.processing_status = ProcessingStatus.FAILED
        resume.save(update_fields=['processing_status'])
        logger.warning('Resume rejected by upload security checks. resume_id=%s', resume.pk)
        return
    if not scan_only and process_resume_and_calculate_match(application) is None:
        raise RuntimeError("Resume evaluation did not finish")


def run_next_resume_work():
    now = timezone.now()
    with transaction.atomic():
        work = (
            ResumeWork.objects.select_for_update(skip_locked=True)
            .filter(failed=False, run_after__lte=now).order_by('run_after', 'pk').first()
        )
        if work is None:
            return False
        work.run_after = now + timedelta(minutes=2)
        work.save(update_fields=['run_after'])

    try:
        if work.kind == ResumeWork.Kind.DELETE:
            default_storage.delete(work.target)
        else:
            evaluate_resume(work.target, scan_only=work.kind == ResumeWork.Kind.SCAN)
    except Exception as exc:
        work.attempts += 1
        work.failed = work.attempts >= MAX_ATTEMPTS
        work.run_after = timezone.now() + timedelta(seconds=30 * 2 ** (work.attempts - 1))
        work.save(update_fields=['attempts', 'failed', 'run_after'])
        if work.failed and work.kind == ResumeWork.Kind.EVALUATE:
            Resume.objects.filter(application_id=work.target).update(processing_status=ProcessingStatus.FAILED)
        logger.error('Resume work failed. id=%s kind=%s attempt=%s error=%s',
                     work.pk, work.kind, work.attempts, type(exc).__name__)
    else:
        work.delete()
    return True
