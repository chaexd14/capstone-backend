from collections import Counter
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone as utc
from math import isfinite

from django.db.models import Case, CharField, Value, When
from django.db.models.functions import Substr
from django.utils import timezone
from rest_framework import serializers

from apps.applications.models import ApplicationStatus
from apps.resumes.models import ProcessingStatus
from apps.resumes.redaction_service import redact_raw_resume_text


MAX_APPLICATIONS = 5000
SHORTLIST_STATUSES = {ApplicationStatus.SHORTLISTED, ApplicationStatus.INTERVIEW}
IDENTITY_REVEAL_STATUSES = {
    ApplicationStatus.SHORTLISTED,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.HIRED,
}
RUBRIC = (
    ('Required skills', 'skill_match_score'),
    ('Experience', 'experience_match_score'),
    ('Education', 'education_match_score'),
    ('Preferred skills', 'preferred_skill_match_score'),
    ('Projects', 'project_match_score'),
)


class ReportRequestSerializer(serializers.Serializer):
    file_type = serializers.ChoiceField(choices=('pdf', 'xlsx'))
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    scope = serializers.ChoiceField(choices=('pipeline', 'status', 'selected'), default='pipeline')
    status = serializers.ChoiceField(choices=ApplicationStatus.choices, required=False)
    application_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, allow_empty=False, max_length=500,
    )

    def validate(self, attrs):
        start, end = attrs.get('date_from'), attrs.get('date_to')
        today = timezone.now().date()
        if (start and start > today) or (end and end > today):
            raise serializers.ValidationError("Application dates must be today or earlier (UTC).")
        if start and end and start > end:
            raise serializers.ValidationError("The start date must be on or before the end date.")
        scope = attrs.get('scope', 'pipeline')
        if scope == 'status' and not attrs.get('status'):
            raise serializers.ValidationError({'status': 'Choose a pipeline stage.'})
        if scope == 'selected' and not attrs.get('application_ids'):
            raise serializers.ValidationError({'application_ids': 'Select at least one candidate.'})
        if attrs.get('application_ids'):
            attrs['application_ids'] = list(dict.fromkeys(attrs['application_ids']))
        return attrs


@dataclass(frozen=True)
class ReportRow:
    code: str
    applied: datetime
    status: str
    evaluation: str
    score: float | None
    rubric_scores: tuple[float | None, ...]
    evidence: str = ''
    verify: str = ''


@dataclass(frozen=True)
class PipelineReport:
    title: str
    department: str
    job_id: int
    period: str
    generated_at: datetime
    counts: tuple[tuple[str, int], ...]
    applications: tuple[ReportRow, ...]
    shortlist: tuple[ReportRow, ...]


def _score(value):
    return round(value, 1) if value is not None and isfinite(value) and 0 <= value <= 100 else None


def _review_details(app, match, requirements):
    if not match:
        return '', ''
    matched = {item.casefold() for item in match.matched_skills if isinstance(item, str)} if isinstance(match.matched_skills, list) else set()
    missing = {item.casefold() for item in match.missing_skills if isinstance(item, str)} if isinstance(match.missing_skills, list) else set()
    matched_labels = [label for key, label in requirements.items() if key in matched]
    missing_labels = [label for key, label in requirements.items() if key in missing]

    insights = match.ai_insights if isinstance(match.ai_insights, dict) else {}
    breakdown = insights.get('must_have_breakdown')
    evidence = ''
    applicant = {'first_name': app.first_name, 'last_name': app.last_name,
                 'email': app.email, 'phone': app.phone}
    reference = ' '.join(redact_raw_resume_text(app.report_evidence_text, applicant).split()).casefold()
    for item in breakdown if isinstance(breakdown, list) else ():
        if not isinstance(item, dict) or not isinstance(item.get('skill'), str):
            continue
        key = item['skill'].casefold()
        if key not in requirements:
            continue
        if item.get('status') in ('unclear', 'not found') and requirements[key] not in missing_labels:
            missing_labels.append(requirements[key])
        excerpt = item.get('evidence')
        if not evidence and item.get('status') == 'met' and isinstance(excerpt, str):
            cleaned = ' '.join(redact_raw_resume_text(excerpt, applicant).split())
            if 12 <= len(cleaned) <= 220 and cleaned.casefold() in reference:
                evidence = f'{requirements[key]} — {cleaned}'
    if not evidence and matched_labels:
        evidence = 'Matched criteria include ' + ', '.join(matched_labels[:3])
    return evidence, ', '.join(missing_labels[:5])


def build_pipeline_report(job, date_from=None, date_to=None, status=None, application_ids=None):
    generated_at = timezone.now()
    applications = job.applications.filter(applied_at__lte=generated_at)
    if status == ApplicationStatus.SUBMITTED:
        applications = applications.filter(status__in=(ApplicationStatus.SUBMITTED, ApplicationStatus.PROCESSING))
    elif status:
        applications = applications.filter(status=status)
    if application_ids:
        requested_ids = set(application_ids)
        available_ids = set(applications.filter(pk__in=requested_ids).values_list('pk', flat=True))
        if available_ids != requested_ids:
            raise serializers.ValidationError(
                'One or more selected candidates are outside this job or report scope.'
            )
        applications = applications.filter(pk__in=requested_ids)
    if date_from:
        applications = applications.filter(applied_at__gte=datetime.combine(date_from, time.min, utc.utc))
    if date_to:
        applications = applications.filter(
            applied_at__lt=datetime.combine(date_to + timedelta(days=1), time.min, utc.utc)
        )
    if not applications.exists():
        raise serializers.ValidationError('No candidates match this report selection.')
    applications = applications.select_related('resume', 'match_result').defer(
        'notes', 'tracking_token', 'resume__file', 'resume__extracted_text', 'resume__redacted_data',
        'resume__redacted_text', 'match_result__explanation',
    ).annotate(report_evidence_text=Case(
        When(status__in=SHORTLIST_STATUSES, then=Substr('resume__redacted_text', 1, 24_000)),
        default=Value(''), output_field=CharField(),
    )).order_by('-applied_at', '-pk')[:MAX_APPLICATIONS + 1]

    period = 'All application dates'
    if date_from or date_to:
        period = f"{date_from or 'Earliest'} to {date_to or generated_at.date()} (UTC)"
    qualifications = (job.required_skills, job.preferred_skills)
    requirements = {skill.casefold(): skill for group in qualifications if isinstance(group, list)
                    for skill in group if isinstance(skill, str)}
    counts = Counter()
    rows, shortlist = [], []
    for app in applications.iterator(chunk_size=100):
        if len(rows) == MAX_APPLICATIONS:
            raise serializers.ValidationError(
                f"Choose a narrower date range to export up to {MAX_APPLICATIONS:,} applications."
            )
        counts[app.status] += 1
        resume = getattr(app, 'resume', None)
        match = getattr(app, 'match_result', None)
        evaluation = resume.processing_status if resume else None
        if evaluation in (ProcessingStatus.PENDING, ProcessingStatus.PROCESSING, ProcessingStatus.FAILED):
            match = None
        evaluation_label = (
            ProcessingStatus(evaluation).label if evaluation in ProcessingStatus.values
            else 'Complete' if match else 'Pending'
        )
        if not match and evaluation_label == 'Completed':
            evaluation_label = 'Unavailable'
        shortlisted = app.status in SHORTLIST_STATUSES
        evidence, verify = _review_details(app, match, requirements) if shortlisted else ('', '')
        identity = app.applicant_name if app.status in IDENTITY_REVEAL_STATUSES else app.candidate_code
        row = ReportRow(
            identity, app.applied_at.astimezone(utc.utc), app.get_status_display(),
            evaluation_label, _score(match.match_score) if match else None,
            tuple(_score(getattr(match, attr)) if match else None for _, attr in RUBRIC),
            evidence, verify,
        )
        rows.append(row)
        if shortlisted:
            shortlist.append(row)
    shortlist.sort(key=lambda row: (row.score is None, -(row.score or 0), row.code))
    return PipelineReport(
        job.title, job.department, job.pk, period, generated_at,
        tuple((label, counts[status]) for status, label in ApplicationStatus.choices),
        tuple(rows), tuple(shortlist),
    )
