from html import escape
from io import BytesIO
from textwrap import wrap

from django.utils.text import slugify

from apps.jobs.report_data import RUBRIC


REPORT_TITLE = 'Recruitment Pipeline & Shortlist'
NAVY = '#1E293B'
BLUE = '#1976D2'
PALE_BLUE = '#EDF4FC'
SLATE = '#475569'
BORDER = '#E2E8F0'
PDF_SHORTLIST_LIMIT = 20
PDF_MIME = 'application/pdf'
EXCEL_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
SNAPSHOT_NOTE = 'Current status of applications in the selected date range.'
SCORING_NOTE = 'AI match scores support review. Shortlist and interview stages reflect recruiter decisions.'


def report_filename(report, file_type):
    title = slugify(report.title)[:60] or f'job-{report.job_id}'
    return f'talentmatch-{title}-pipeline-{report.generated_at:%Y-%m-%d}.{file_type}'


def render_pipeline_pdf(report):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, title=REPORT_TITLE, author='TalentMatch',
        topMargin=51, bottomMargin=49, leftMargin=42, rightMargin=42,
    )
    title_style = ParagraphStyle('Title', fontName='Helvetica-Bold', fontSize=24, leading=29, textColor=colors.HexColor(NAVY))
    heading_style = ParagraphStyle('Heading', fontName='Helvetica-Bold', fontSize=13, leading=16,
                                   textColor=colors.HexColor(NAVY), keepWithNext=True)
    body_style = ParagraphStyle('Body', fontSize=10, leading=15, textColor=colors.HexColor(SLATE))
    cell_style = ParagraphStyle('Cell', fontSize=9, leading=13, textColor=colors.HexColor(NAVY))

    def table(headers, rows, widths):
        grid = Table([headers, *rows], colWidths=widths, repeatRows=1, hAlign='LEFT')
        grid.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(PALE_BLUE)),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor(NAVY)),
            ('LINEBELOW', (0, 0), (-1, -1), 0.4, colors.HexColor(BORDER)),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 9),
            ('TOPPADDING', (0, 0), (-1, -1), 7),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ]))
        return grid

    def frame(canvas, _):
        width, height = A4
        canvas.setStrokeColor(colors.HexColor(BLUE))
        canvas.setLineWidth(2)
        canvas.line(42, height - 33, width - 42, height - 33)
        canvas.setFillColor(colors.HexColor(SLATE))
        canvas.setFont('Helvetica', 8)
        canvas.drawString(42, 27, 'TalentMatch  /  Confidential recruitment report')
        canvas.drawRightString(width - 42, 27, str(canvas.getPageNumber()))

    story = [
        Paragraph(escape(REPORT_TITLE), title_style),
        Spacer(1, 9),
        Paragraph(f'<b>{escape(report.title)}</b> · {escape(report.department or "Department unspecified")} · Job #{report.job_id}', body_style),
        Paragraph(f'{escape(report.period)} · Generated {report.generated_at:%Y-%m-%d %H:%M} UTC', body_style),
        Spacer(1, 22),
        Paragraph('Current pipeline', heading_style),
        Paragraph(SNAPSHOT_NOTE, body_style),
        Spacer(1, 7),
        table(['Recruiter stage', 'Applications'], [('Total applications', len(report.applications)), *report.counts], [373, 125]),
        Spacer(1, 21),
        Paragraph('Shortlist & interviews', heading_style),
        Paragraph(SCORING_NOTE, body_style),
        Spacer(1, 7),
    ]
    if report.shortlist:
        if len(report.shortlist) > PDF_SHORTLIST_LIMIT:
            story.extend([
                Paragraph(f'Showing {PDF_SHORTLIST_LIMIT} of {len(report.shortlist)} candidates. The Excel report includes the full shortlist.', body_style),
                Spacer(1, 7),
            ])
        rows = []
        for row in report.shortlist[:PDF_SHORTLIST_LIMIT]:
            evidence = escape(row.evidence or 'No grounded excerpt available.')
            verification = escape(row.verify or 'Review in the candidate dossier.')
            details = Paragraph(f'{evidence}<br/><b>Verify</b> {verification}', cell_style)
            score = f'{row.score:.1f}%' if row.score is not None else row.evaluation
            rows.append((row.code, row.status, score, details))
        story.append(table(['Candidate', 'Stage', 'AI match', 'Screening evidence'], rows, [77, 86, 74, 261]))
    else:
        story.append(Paragraph('No candidates in this selection are currently shortlisted or at interview.', body_style))
    doc.build(story, onFirstPage=frame, onLaterPages=frame)
    return buffer.getvalue()


def render_pipeline_excel(report):
    from xlsxwriter import Workbook

    buffer = BytesIO()
    with Workbook(buffer, {
        'in_memory': True, 'strings_to_formulas': False, 'strings_to_urls': False,
    }) as workbook:
        workbook.set_properties({'title': REPORT_TITLE, 'author': 'TalentMatch'})
        summary, applications, shortlist = (
            workbook.add_worksheet(name) for name in ('Summary', 'Applications', 'Shortlist')
        )
        heading = workbook.add_format({'font_color': NAVY, 'bold': True, 'font_size': 18})
        header = workbook.add_format({'bg_color': PALE_BLUE, 'font_color': NAVY, 'bold': True, 'bottom': 1, 'bottom_color': BORDER})
        note = workbook.add_format({'font_color': SLATE, 'text_wrap': True, 'valign': 'top'})
        percent = workbook.add_format({'num_format': '0.0%'})
        date_style = workbook.add_format({'num_format': 'yyyy-mm-dd hh:mm'})
        plain_wrap = workbook.add_format({'text_wrap': True, 'valign': 'top'})

        summary.hide_gridlines(2)
        summary.set_column('A:A', 31)
        summary.set_column('B:B', 24)
        summary.set_column('C:D', 24)
        summary.merge_range('A1:D1', REPORT_TITLE, heading)
        summary.set_row(0, 31)
        for row_index, line in enumerate((
            f'{report.title} · {report.department or "Department unspecified"} · Job #{report.job_id}',
            f'{report.period} · Generated {report.generated_at:%Y-%m-%d %H:%M} UTC',
            SNAPSHOT_NOTE, SCORING_NOTE,
        ), start=2):
            summary.merge_range(row_index, 0, row_index, 3, line, note)
            summary.set_row(row_index, 28 if row_index == 2 else 19)
        summary.write_row(7, 0, ('Recruiter stage', 'Applications'), header)
        summary.write_row(8, 0, ('Total applications', len(report.applications)))
        for index, row in enumerate(report.counts, start=9):
            summary.write_row(index, 0, row)

        def prepare(sheet, columns, widths, row_count):
            sheet.hide_gridlines(2)
            sheet.freeze_panes(1, 1)
            sheet.set_row(0, 24)
            sheet.write_row(0, 0, columns, header)
            for index, width in enumerate(widths):
                sheet.set_column(index, index, width)
            sheet.autofilter(0, 0, max(1, row_count), len(columns) - 1)
            if row_count == 0:
                sheet.write(2, 0, 'No matching applications.')

        def write_score(sheet, row, column, score, fallback=''):
            if score is None:
                sheet.write(row, column, fallback)
            else:
                sheet.write_number(row, column, score / 100, percent)

        prepare(applications, ('Candidate', 'Applied at (UTC)', 'Recruiter stage', 'Evaluation', 'AI match',
                               *(label for label, _ in RUBRIC)),
                (17, 22, 20, 17, 15, 19, 16, 16, 19, 16), len(report.applications))
        for index, row in enumerate(report.applications, start=1):
            applications.write(index, 0, row.code)
            applications.write_datetime(index, 1, row.applied.replace(tzinfo=None), date_style)
            applications.write_row(index, 2, (row.status, row.evaluation))
            write_score(applications, index, 4, row.score, row.evaluation)
            for column, score in enumerate(row.rubric_scores, start=5):
                write_score(applications, index, column, score)

        prepare(shortlist, ('Candidate', 'Recruiter stage', 'AI match', 'Screening evidence', 'Qualifications to verify'),
                (17, 20, 15, 66, 48), len(report.shortlist))
        for index, row in enumerate(report.shortlist, start=1):
            shortlist.write_row(index, 0, (row.code, row.status))
            write_score(shortlist, index, 2, row.score, row.evaluation)
            evidence = row.evidence or 'No grounded excerpt available.'
            verify = row.verify or 'Review in the candidate dossier.'
            shortlist.write_string(index, 3, evidence, plain_wrap)
            shortlist.write_string(index, 4, verify, plain_wrap)
            lines = max(len(wrap(evidence, width=60)), len(wrap(verify, width=42)))
            shortlist.set_row(index, min(400, max(29, lines * 16)))
    return buffer.getvalue()
