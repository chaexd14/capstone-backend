from collections import Counter
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone as utc
from html import escape
from io import BytesIO
from math import isfinite

from django.utils import timezone
from rest_framework import serializers

from apps.applications.models import ApplicationStatus
from apps.jobs.models import Job
from apps.jobs.report_data import MAX_APPLICATIONS
from apps.resumes.models import ProcessingStatus


MATCH_BAND_CHOICES = (
    ('ALL', 'All match scores'),
    ('STRONG', 'Strong (85%+)'),
    ('GOOD', 'Good (70%+)'),
    ('PARTIAL', 'Partial (50%+)'),
    ('WEAK', 'Below 50%'),
)
MATCH_BAND_LABELS = dict(MATCH_BAND_CHOICES)

PDF_MIME = 'application/pdf'
EXCEL_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

NAVY = '#1E293B'
BLUE = '#1976D2'
PALE_BLUE = '#EDF4FC'
SLATE = '#475569'
BORDER = '#E2E8F0'
ROW_ALT = '#F8FAFC'


class AllApplicationsReportRequestSerializer(serializers.Serializer):
    file_type = serializers.ChoiceField(choices=('pdf', 'xlsx'))
    date_from = serializers.DateField(required=False, allow_null=True)
    date_to = serializers.DateField(required=False, allow_null=True)
    status = serializers.ChoiceField(choices=ApplicationStatus.choices, required=False, allow_null=True)
    match_band = serializers.ChoiceField(
        choices=[choice for choice, _ in MATCH_BAND_CHOICES],
        default='ALL',
        required=False,
    )
    job_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)

    def validate(self, attrs):
        start, end = attrs.get('date_from'), attrs.get('date_to')
        today = timezone.now().date()
        if (start and start > today) or (end and end > today):
            raise serializers.ValidationError("Application dates must be today or earlier (UTC).")
        if start and end and start > end:
            raise serializers.ValidationError("The start date must be on or before the end date.")
        return attrs


@dataclass(frozen=True)
class ApplicationReportRow:
    code: str
    full_name: str
    email: str
    phone: str
    job_title: str
    department: str
    applied: datetime
    status: str
    evaluation: str
    score: float | None


@dataclass(frozen=True)
class AllApplicationsReport:
    period: str
    status_label: str
    match_label: str
    job_label: str
    generated_at: datetime
    counts: tuple[tuple[str, int], ...]
    rows: tuple[ApplicationReportRow, ...]


def _score(value):
    return round(value, 1) if value is not None and isfinite(value) and 0 <= value <= 100 else None


def _evaluation_label(resume, score):
    if score is not None:
        return 'Scored'
    processing = getattr(resume, 'processing_status', None)
    if processing == ProcessingStatus.FAILED:
        return 'Failed'
    if processing == ProcessingStatus.PROCESSING:
        return 'Processing'
    return 'Pending'


def build_applications_report(
    base_queryset,
    *,
    date_from=None,
    date_to=None,
    status=None,
    match_band='ALL',
    job_id=None,
):
    generated_at = timezone.now()
    queryset = base_queryset.filter(applied_at__lte=generated_at)

    if status:
        queryset = queryset.filter(status=status)
    if job_id:
        queryset = queryset.filter(job_id=job_id)
    if date_from:
        queryset = queryset.filter(applied_at__gte=datetime.combine(date_from, time.min, utc.utc))
    if date_to:
        queryset = queryset.filter(
            applied_at__lt=datetime.combine(date_to + timedelta(days=1), time.min, utc.utc)
        )

    if match_band == 'STRONG':
        queryset = queryset.filter(match_result__match_score__gte=85)
    elif match_band == 'GOOD':
        queryset = queryset.filter(match_result__match_score__gte=70)
    elif match_band == 'PARTIAL':
        queryset = queryset.filter(match_result__match_score__gte=50)
    elif match_band == 'WEAK':
        queryset = queryset.filter(match_result__match_score__lt=50)

    if not queryset.exists():
        raise serializers.ValidationError('No applications match this report selection.')

    queryset = (
        queryset.select_related('job', 'resume', 'match_result')
        .defer(
            'notes',
            'tracking_token',
            'resume__file',
            'resume__extracted_text',
            'resume__redacted_data',
            'resume__redacted_text',
            'match_result__explanation',
            'match_result__ai_insights',
        )
        .order_by('-applied_at', '-pk')[: MAX_APPLICATIONS + 1]
    )

    counts = Counter()
    rows = []
    for app in queryset.iterator(chunk_size=100):
        if len(rows) == MAX_APPLICATIONS:
            raise serializers.ValidationError(
                f'Report exceeds {MAX_APPLICATIONS} applications; narrow the filters and try again.'
            )
        match = getattr(app, 'match_result', None)
        resume = getattr(app, 'resume', None)
        score = _score(match.match_score) if match else None
        status_display = app.get_status_display()
        counts[status_display] += 1
        full_name = f"{app.first_name} {app.last_name}".strip() or app.candidate_code
        rows.append(
            ApplicationReportRow(
                code=app.candidate_code,
                full_name=full_name,
                email=app.email or '',
                phone=app.phone or '',
                job_title=app.job.title if app.job else f'Requisition #{app.job_id}',
                department=(app.job.department if app.job else '') or 'General recruitment',
                applied=app.applied_at,
                status=status_display,
                evaluation=_evaluation_label(resume, score),
                score=score,
            )
        )

    period = 'All application dates'
    if date_from or date_to:
        period = f"{date_from or 'Earliest'} to {date_to or generated_at.date()} (UTC)"

    status_label = dict(ApplicationStatus.choices).get(status, 'All statuses') if status else 'All statuses'
    match_label = MATCH_BAND_LABELS.get(match_band or 'ALL', 'All match scores')
    if job_id:
        job_title = Job.objects.filter(pk=job_id).values_list('title', flat=True).first()
        job_label = job_title or f'Job #{job_id}'
    else:
        job_label = 'All jobs'

    ordered_counts = tuple(
        (label, counts[label]) for _, label in ApplicationStatus.choices if counts[label]
    )
    return AllApplicationsReport(
        period=period,
        status_label=status_label,
        match_label=match_label,
        job_label=job_label,
        generated_at=generated_at,
        counts=ordered_counts,
        rows=tuple(rows),
    )


def applications_report_filename(report, file_type):
    return f'talentmatch-all-applications-{report.generated_at:%Y-%m-%d}.{file_type}'


def render_applications_pdf(report):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    page_size = landscape(A4)
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=page_size,
        title='All Applications Report',
        author='TalentMatch',
        topMargin=50,
        bottomMargin=46,
        leftMargin=36,
        rightMargin=36,
    )
    title_style = ParagraphStyle(
        'Title', fontName='Helvetica-Bold', fontSize=20, leading=24, textColor=colors.HexColor(NAVY)
    )
    heading_style = ParagraphStyle(
        'Heading',
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=15,
        textColor=colors.HexColor(NAVY),
        keepWithNext=True,
    )
    body_style = ParagraphStyle('Body', fontSize=9.5, leading=14, textColor=colors.HexColor(SLATE))
    cell_style = ParagraphStyle('Cell', fontSize=8.5, leading=11.5, textColor=colors.HexColor(NAVY))

    def build_table(headers, table_rows, widths):
        grid = Table([headers, *table_rows], colWidths=widths, repeatRows=1, hAlign='LEFT')
        grid.setStyle(
            TableStyle(
                [
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(PALE_BLUE)),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, -1), 8.5),
                    ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor(NAVY)),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor(ROW_ALT)]),
                    ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor(BORDER)),
                    ('INNERGRID', (0, 0), (-1, -1), 0.4, colors.HexColor(BORDER)),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 6),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                    ('TOPPADDING', (0, 0), (-1, -1), 6),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ]
            )
        )
        return grid

    def frame(canvas, _):
        width, height = page_size
        canvas.setStrokeColor(colors.HexColor(BLUE))
        canvas.setLineWidth(2)
        canvas.line(36, height - 30, width - 36, height - 30)
        canvas.setFillColor(colors.HexColor(SLATE))
        canvas.setFont('Helvetica', 8)
        canvas.drawString(36, 24, 'TalentMatch  /  Confidential applications report')
        canvas.drawRightString(width - 36, 24, str(canvas.getPageNumber()))

    story = [
        Paragraph('All Applications Report', title_style),
        Spacer(1, 6),
        Paragraph(
            f'<b>Scope:</b> {escape(report.job_label)} · <b>Status:</b> {escape(report.status_label)} · '
            f'<b>Match:</b> {escape(report.match_label)}',
            body_style,
        ),
        Paragraph(
            f'<b>Period:</b> {escape(report.period)} · Generated {report.generated_at:%Y-%m-%d %H:%M} UTC',
            body_style,
        ),
        Spacer(1, 16),
        Paragraph('Application summary', heading_style),
        Spacer(1, 6),
        build_table(
            ['Status', 'Applications'],
            [('Total matching applications', len(report.rows)), *report.counts],
            [240, 120],
        ),
        Spacer(1, 16),
        Paragraph('Applicant roster', heading_style),
        Spacer(1, 6),
        build_table(
            ['Applicant #', 'Candidate', 'Email', 'Phone', 'Applied Position', 'Department', 'AI Match', 'Date Applied', 'Status'],
            [
                (
                    Paragraph(escape(row.code), cell_style),
                    Paragraph(escape(row.full_name), cell_style),
                    Paragraph(escape(row.email or '—'), cell_style),
                    Paragraph(escape(row.phone or '—'), cell_style),
                    Paragraph(escape(row.job_title), cell_style),
                    Paragraph(escape(row.department), cell_style),
                    f'{row.score:.1f}%' if row.score is not None else row.evaluation,
                    row.applied.strftime('%Y-%m-%d'),
                    Paragraph(escape(row.status), cell_style),
                )
                for row in report.rows
            ],
            [62, 88, 128, 72, 120, 80, 54, 68, 76],
        ),
    ]
    doc.build(story, onFirstPage=frame, onLaterPages=frame)
    return buffer.getvalue()


def render_applications_excel(report):
    from xlsxwriter import Workbook

    buffer = BytesIO()
    with Workbook(
        buffer,
        {'in_memory': True, 'strings_to_formulas': False, 'strings_to_urls': False},
    ) as workbook:
        workbook.set_properties({'title': 'All Applications Report', 'author': 'TalentMatch'})
        sheet = workbook.add_worksheet('Applications Report')

        heading = workbook.add_format({'font_color': NAVY, 'bold': True, 'font_size': 18, 'valign': 'vcenter'})
        section_heading = workbook.add_format({'font_color': NAVY, 'bold': True, 'font_size': 12, 'valign': 'vcenter'})
        header = workbook.add_format({
            'bg_color': PALE_BLUE,
            'font_color': NAVY,
            'bold': True,
            'border': 1,
            'border_color': BORDER,
            'valign': 'vcenter',
        })
        meta_label = workbook.add_format({
            'bg_color': ROW_ALT,
            'font_color': NAVY,
            'bold': True,
            'border': 1,
            'border_color': BORDER,
            'valign': 'vcenter',
        })
        meta_value = workbook.add_format({
            'font_color': SLATE,
            'border': 1,
            'border_color': BORDER,
            'text_wrap': True,
            'valign': 'vcenter',
        })
        total_label = workbook.add_format({
            'bg_color': ROW_ALT,
            'font_color': NAVY,
            'bold': True,
            'border': 1,
            'border_color': BORDER,
            'valign': 'vcenter',
        })
        total_count = workbook.add_format({
            'bg_color': ROW_ALT,
            'font_color': NAVY,
            'bold': True,
            'border': 1,
            'border_color': BORDER,
            'num_format': '#,##0',
            'valign': 'vcenter',
        })

        def make_row_formats(bg_color=None):
            base = {
                'border': 1,
                'border_color': BORDER,
                'font_color': NAVY,
                'valign': 'vcenter',
            }
            if bg_color:
                base['bg_color'] = bg_color
            return {
                'text': workbook.add_format(base),
                'count': workbook.add_format({**base, 'num_format': '#,##0'}),
                'percent': workbook.add_format({**base, 'num_format': '0.0%'}),
                'date': workbook.add_format({**base, 'num_format': 'yyyy-mm-dd'}),
            }

        row_formats = (make_row_formats(), make_row_formats(ROW_ALT))

        columns = (
            'Applicant #',
            'Candidate',
            'Email',
            'Phone',
            'Applied Position',
            'Department',
            'AI Match',
            'Date Applied',
            'Status',
        )
        widths = (15, 22, 28, 16, 26, 18, 12, 14, 15)
        for col_idx, width in enumerate(widths):
            sheet.set_column(col_idx, col_idx, width)

        sheet.set_row(0, 28)
        sheet.merge_range(0, 0, 0, len(columns) - 1, 'All Applications Report', heading)

        metadata = (
            ('Job scope', report.job_label),
            ('Status filter', report.status_label),
            ('Match score filter', report.match_label),
            ('Date range', report.period),
            ('Generated (UTC)', f'{report.generated_at:%Y-%m-%d %H:%M} UTC'),
        )
        for idx, (label, val) in enumerate(metadata, start=2):
            sheet.set_row(idx, 20)
            sheet.write(idx, 0, label, meta_label)
            sheet.merge_range(idx, 1, idx, len(columns) - 1, val, meta_value)

        row_cursor = 8
        sheet.set_row(row_cursor, 22)
        sheet.merge_range(row_cursor, 0, row_cursor, 2, 'Application summary', section_heading)
        row_cursor += 1
        sheet.set_row(row_cursor, 22)
        sheet.write_row(row_cursor, 0, ('Status', 'Applications'), header)
        row_cursor += 1
        sheet.set_row(row_cursor, 20)
        sheet.write(row_cursor, 0, 'Total matching applications', total_label)
        sheet.write_number(row_cursor, 1, len(report.rows), total_count)
        row_cursor += 1

        for offset, (status_label, status_count) in enumerate(report.counts):
            fmt = row_formats[offset % 2]
            sheet.set_row(row_cursor, 20)
            sheet.write(row_cursor, 0, status_label, fmt['text'])
            sheet.write_number(row_cursor, 1, status_count, fmt['count'])
            row_cursor += 1

        row_cursor += 1
        sheet.set_row(row_cursor, 22)
        sheet.merge_range(row_cursor, 0, row_cursor, len(columns) - 1, 'Applicant roster', section_heading)
        row_cursor += 1

        roster_header_row = row_cursor
        sheet.set_row(roster_header_row, 24)
        sheet.write_row(roster_header_row, 0, columns, header)
        sheet.autofilter(
            roster_header_row,
            0,
            roster_header_row + max(1, len(report.rows)),
            len(columns) - 1,
        )

        for offset, row in enumerate(report.rows):
            target_row = roster_header_row + 1 + offset
            fmt = row_formats[offset % 2]
            sheet.set_row(target_row, 20)
            sheet.write_row(
                target_row,
                0,
                (
                    row.code,
                    row.full_name,
                    row.email or '—',
                    row.phone or '—',
                    row.job_title,
                    row.department,
                ),
                fmt['text'],
            )
            if row.score is None:
                sheet.write(target_row, 6, row.evaluation, fmt['text'])
            else:
                sheet.write_number(target_row, 6, row.score / 100, fmt['percent'])
            sheet.write_datetime(target_row, 7, row.applied.replace(tzinfo=None), fmt['date'])
            sheet.write(target_row, 8, row.status, fmt['text'])

    return buffer.getvalue()

