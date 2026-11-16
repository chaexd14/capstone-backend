from html import escape
from io import BytesIO
from textwrap import wrap

from django.utils.text import slugify


REPORT_TITLE = 'Recruitment Pipeline & Shortlist'
NAVY = '#1E293B'
BLUE = '#1976D2'
PALE_BLUE = '#EDF4FC'
SLATE = '#475569'
BORDER = '#E2E8F0'
ROW_ALT = '#F8FAFC'
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
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor(ROW_ALT)]),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor(BORDER)),
            ('INNERGRID', (0, 0), (-1, -1), 0.4, colors.HexColor(BORDER)),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
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
        Paragraph('Candidate roster', heading_style),
        Paragraph('Candidates included in this report selection. Contact details are omitted.', body_style),
        Spacer(1, 7),
        table(
            ['Candidate', 'Applied (UTC)', 'Stage', 'AI match'],
            [
                (
                    row.code,
                    row.applied.strftime('%Y-%m-%d %H:%M'),
                    row.status,
                    f'{row.score:.1f}%' if row.score is not None else row.evaluation,
                )
                for row in report.applications
            ],
            [120, 120, 130, 128],
        ),
        Spacer(1, 21),
        Paragraph('Shortlist & interviews', heading_style),
        Paragraph(SCORING_NOTE, body_style),
        Spacer(1, 7),
    ]
    if report.shortlist:
        rows = []
        for row in report.shortlist:
            evidence = Paragraph(escape(row.evidence or 'No grounded excerpt available.'), cell_style)
            verification = Paragraph(escape(row.verify or 'Review in the candidate dossier.'), cell_style)
            score = f'{row.score:.1f}%' if row.score is not None else row.evaluation
            rows.append((row.code, row.status, score, evidence, verification))
        story.append(
            table(
                ['Candidate', 'Stage', 'AI match', 'Screening evidence', 'Qualifications to verify'],
                rows,
                [65, 72, 54, 175, 132],
            )
        )
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
        sheet = workbook.add_worksheet('Pipeline Report')

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
        note = workbook.add_format({
            'bg_color': ROW_ALT,
            'font_color': SLATE,
            'border': 1,
            'border_color': BORDER,
            'text_wrap': True,
            'valign': 'vcenter',
        })
        body_note = workbook.add_format({'font_color': SLATE, 'valign': 'vcenter'})
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
                'date': workbook.add_format({**base, 'num_format': 'yyyy-mm-dd hh:mm'}),
                'wrap': workbook.add_format({**base, 'text_wrap': True, 'valign': 'top'}),
                'shortlist_text': workbook.add_format({**base, 'valign': 'top'}),
                'shortlist_percent': workbook.add_format({**base, 'num_format': '0.0%', 'valign': 'top'}),
            }

        row_formats = (make_row_formats(), make_row_formats(ROW_ALT))

        widths = (18, 22, 18, 56, 44)
        for col_idx, width in enumerate(widths):
            sheet.set_column(col_idx, col_idx, width)

        sheet.set_row(0, 31)
        sheet.merge_range('A1:E1', REPORT_TITLE, heading)
        for row_index, line in enumerate((
            f'{report.title} · {report.department or "Department unspecified"} · Job #{report.job_id}',
            f'{report.period} · Generated {report.generated_at:%Y-%m-%d %H:%M} UTC',
            SNAPSHOT_NOTE,
            SCORING_NOTE,
        ), start=2):
            sheet.merge_range(row_index, 0, row_index, 4, line, note)
            sheet.set_row(row_index, 24 if row_index == 2 else 20)

        row_cursor = 7
        sheet.set_row(row_cursor, 22)
        sheet.merge_range(row_cursor, 0, row_cursor, 4, 'Current pipeline', section_heading)
        row_cursor += 1
        sheet.set_row(row_cursor, 22)
        sheet.write_row(row_cursor, 0, ('Recruiter stage', 'Applications'), header)
        row_cursor += 1
        sheet.set_row(row_cursor, 20)
        sheet.write(row_cursor, 0, 'Total applications', total_label)
        sheet.write_number(row_cursor, 1, len(report.applications), total_count)
        row_cursor += 1

        for offset, (stage_label, stage_count) in enumerate(report.counts):
            fmt = row_formats[offset % 2]
            sheet.set_row(row_cursor, 20)
            sheet.write(row_cursor, 0, stage_label, fmt['text'])
            sheet.write_number(row_cursor, 1, stage_count, fmt['count'])
            row_cursor += 1

        row_cursor += 1
        sheet.set_row(row_cursor, 22)
        sheet.merge_range(row_cursor, 0, row_cursor, 4, 'Candidate roster', section_heading)
        row_cursor += 1
        sheet.set_row(row_cursor, 18)
        sheet.merge_range(row_cursor, 0, row_cursor, 4, 'Candidates included in this report selection. Contact details are omitted.', body_note)
        row_cursor += 1

        roster_header_row = row_cursor
        sheet.set_row(roster_header_row, 24)
        sheet.write_row(roster_header_row, 0, ('Candidate', 'Applied (UTC)', 'Stage', 'AI match'), header)
        sheet.autofilter(
            roster_header_row,
            0,
            roster_header_row + max(1, len(report.applications)),
            3,
        )
        row_cursor += 1

        if report.applications:
            for offset, row in enumerate(report.applications):
                fmt = row_formats[offset % 2]
                sheet.set_row(row_cursor, 20)
                sheet.write(row_cursor, 0, row.code, fmt['text'])
                sheet.write_datetime(row_cursor, 1, row.applied.replace(tzinfo=None), fmt['date'])
                sheet.write(row_cursor, 2, row.status, fmt['text'])
                if row.score is None:
                    sheet.write(row_cursor, 3, row.evaluation, fmt['text'])
                else:
                    sheet.write_number(row_cursor, 3, row.score / 100, fmt['percent'])
                row_cursor += 1
        else:
            sheet.set_row(row_cursor, 20)
            sheet.merge_range(row_cursor, 0, row_cursor, 3, 'No matching applications.', row_formats[0]['text'])
            row_cursor += 1

        row_cursor += 1
        sheet.set_row(row_cursor, 22)
        sheet.merge_range(row_cursor, 0, row_cursor, 4, 'Shortlist & interviews', section_heading)
        row_cursor += 1

        if report.shortlist:
            sheet.set_row(row_cursor, 24)
            sheet.write_row(
                row_cursor,
                0,
                ('Candidate', 'Stage', 'AI match', 'Screening evidence', 'Qualifications to verify'),
                header,
            )
            row_cursor += 1
            for offset, row in enumerate(report.shortlist):
                fmt = row_formats[offset % 2]
                sheet.write_row(row_cursor, 0, (row.code, row.status), fmt['shortlist_text'])
                if row.score is None:
                    sheet.write(row_cursor, 2, row.evaluation, fmt['shortlist_text'])
                else:
                    sheet.write_number(row_cursor, 2, row.score / 100, fmt['shortlist_percent'])
                evidence = row.evidence or 'No grounded excerpt available.'
                verify = row.verify or 'Review in the candidate dossier.'
                sheet.write_string(row_cursor, 3, evidence, fmt['wrap'])
                sheet.write_string(row_cursor, 4, verify, fmt['wrap'])
                lines = max(len(wrap(evidence, width=52)), len(wrap(verify, width=40)))
                sheet.set_row(row_cursor, min(400, max(26, lines * 16)))
                row_cursor += 1
        else:
            sheet.set_row(row_cursor, 20)
            sheet.merge_range(
                row_cursor,
                0,
                row_cursor,
                4,
                'No candidates in this selection are currently shortlisted or at interview.',
                row_formats[0]['text'],
            )

    return buffer.getvalue()


def job_candidates_report_filename(report, file_type):
    title = slugify(report.title)[:60] or f'job-{report.job_id}'
    view_slug = 'ranked-applicants' if report.report_view == 'ranked' else 'all-applicants'
    return f'talentmatch-{title}-{view_slug}-{report.generated_at:%Y-%m-%d}.{file_type}'


def render_job_candidates_pdf(report):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    is_ranked = report.report_view == 'ranked'
    doc_title = 'Ranked Candidates Report' if is_ranked else 'Job Applicants Roster Report'

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, title=doc_title, author='TalentMatch',
        topMargin=51, bottomMargin=49, leftMargin=42, rightMargin=42,
    )
    title_style = ParagraphStyle('Title', fontName='Helvetica-Bold', fontSize=22, leading=27, textColor=colors.HexColor(NAVY))
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
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor(ROW_ALT)]),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor(BORDER)),
            ('INNERGRID', (0, 0), (-1, -1), 0.4, colors.HexColor(BORDER)),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 7),
            ('RIGHTPADDING', (0, 0), (-1, -1), 7),
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
        canvas.drawString(42, 27, 'TalentMatch  /  Confidential job candidates report')
        canvas.drawRightString(width - 42, 27, str(canvas.getPageNumber()))

    story = [
        Paragraph(escape(doc_title), title_style),
        Spacer(1, 9),
        Paragraph(f'<b>{escape(report.title)}</b> · {escape(report.department or "Department unspecified")} · Job #{report.job_id}', body_style),
        Paragraph(f'{escape(report.period)} · Status: {escape(report.status_label)} · Strength: {escape(report.match_label)}', body_style),
        Paragraph(f'Generated {report.generated_at:%Y-%m-%d %H:%M} UTC · Contact details omitted per blind-screening policy.', body_style),
        Spacer(1, 20),
        Paragraph('Selection summary', heading_style),
        Spacer(1, 7),
        table(['Candidate stage', 'Applications'], [('Total matching candidates', len(report.rows)), *report.counts], [373, 125]),
        Spacer(1, 20),
        Paragraph('Ranked applicants' if is_ranked else 'All applicants', heading_style),
        Paragraph(
            'Sorted by AI match score descending.' if is_ranked else 'Sorted by application date descending.',
            body_style,
        ),
        Spacer(1, 7),
    ]

    if is_ranked:
        pdf_rows = [
            (
                str(row.rank or '—'),
                Paragraph(escape(row.code), cell_style),
                row.applied.strftime('%Y-%m-%d'),
                f'{row.score:.1f}%' if row.score is not None else '—',
                row.match_level,
                row.must_haves,
                row.status,
            )
            for row in report.rows
        ]
        story.append(
            table(
                ['Rank', 'Candidate', 'Applied', 'Match', 'Level', 'Must-haves', 'Status'],
                pdf_rows,
                [36, 126, 68, 52, 62, 68, 86],
            )
        )
    else:
        pdf_rows = [
            (
                Paragraph(escape(row.code), cell_style),
                row.applied.strftime('%Y-%m-%d %H:%M'),
                row.status,
                f'{row.score:.1f}%' if row.score is not None else '—',
                row.evaluation,
            )
            for row in report.rows
        ]
        story.append(
            table(
                ['Candidate', 'Applied (UTC)', 'Status', 'AI match', 'Evaluation'],
                pdf_rows,
                [150, 110, 96, 66, 76],
            )
        )

    doc.build(story, onFirstPage=frame, onLaterPages=frame)
    return buffer.getvalue()


def render_job_candidates_excel(report):
    from xlsxwriter import Workbook

    is_ranked = report.report_view == 'ranked'
    doc_title = 'Ranked Candidates Report' if is_ranked else 'Job Applicants Roster Report'

    buffer = BytesIO()
    with Workbook(buffer, {
        'in_memory': True, 'strings_to_formulas': False, 'strings_to_urls': False,
    }) as workbook:
        workbook.set_properties({'title': doc_title, 'author': 'TalentMatch'})
        sheet = workbook.add_worksheet('Ranked Applicants' if is_ranked else 'All Applicants')

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
        note = workbook.add_format({
            'bg_color': ROW_ALT,
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
                'date': workbook.add_format({**base, 'num_format': 'yyyy-mm-dd hh:mm'}),
            }

        row_formats = (make_row_formats(), make_row_formats(ROW_ALT))

        widths = (10, 28, 20, 14, 16, 16, 18) if is_ranked else (28, 22, 18, 16, 18)
        max_col = len(widths) - 1
        for col_idx, width in enumerate(widths):
            sheet.set_column(col_idx, col_idx, width)

        sheet.set_row(0, 31)
        sheet.merge_range(0, 0, 0, max_col, doc_title, heading)
        for row_index, line in enumerate((
            f'{report.title} · {report.department or "Department unspecified"} · Job #{report.job_id}',
            f'{report.period} · Status: {report.status_label} · Strength: {report.match_label}',
            f'Generated {report.generated_at:%Y-%m-%d %H:%M} UTC · Early-stage records use candidate codes; contact details omitted.',
        ), start=2):
            sheet.merge_range(row_index, 0, row_index, max_col, line, note)
            sheet.set_row(row_index, 22)

        row_cursor = 6
        sheet.set_row(row_cursor, 22)
        sheet.merge_range(row_cursor, 0, row_cursor, max_col, 'Selection summary', section_heading)
        row_cursor += 1
        sheet.set_row(row_cursor, 22)
        sheet.write_row(row_cursor, 0, ('Candidate stage', 'Applications'), header)
        row_cursor += 1
        sheet.set_row(row_cursor, 20)
        sheet.write(row_cursor, 0, 'Total matching candidates', total_label)
        sheet.write_number(row_cursor, 1, len(report.rows), total_count)
        row_cursor += 1

        for offset, (stage_label, stage_count) in enumerate(report.counts):
            fmt = row_formats[offset % 2]
            sheet.set_row(row_cursor, 20)
            sheet.write(row_cursor, 0, stage_label, fmt['text'])
            sheet.write_number(row_cursor, 1, stage_count, fmt['count'])
            row_cursor += 1

        row_cursor += 1
        sheet.set_row(row_cursor, 22)
        sheet.merge_range(
            row_cursor,
            0,
            row_cursor,
            max_col,
            'Ranked applicants' if is_ranked else 'All applicants',
            section_heading,
        )
        row_cursor += 1

        headers = (
            ('Rank', 'Candidate', 'Applied (UTC)', 'Match score', 'Match level', 'Must-haves', 'Status')
            if is_ranked
            else ('Candidate', 'Applied (UTC)', 'Status', 'AI match', 'Evaluation')
        )
        sheet.set_row(row_cursor, 24)
        sheet.write_row(row_cursor, 0, headers, header)
        sheet.autofilter(row_cursor, 0, row_cursor + max(1, len(report.rows)), max_col)
        row_cursor += 1

        for offset, row in enumerate(report.rows):
            fmt = row_formats[offset % 2]
            sheet.set_row(row_cursor, 20)
            if is_ranked:
                sheet.write_number(row_cursor, 0, row.rank or (offset + 1), fmt['count'])
                sheet.write(row_cursor, 1, row.code, fmt['text'])
                sheet.write_datetime(row_cursor, 2, row.applied.replace(tzinfo=None), fmt['date'])
                if row.score is None:
                    sheet.write(row_cursor, 3, '—', fmt['text'])
                else:
                    sheet.write_number(row_cursor, 3, row.score / 100, fmt['percent'])
                sheet.write(row_cursor, 4, row.match_level, fmt['text'])
                sheet.write(row_cursor, 5, row.must_haves, fmt['text'])
                sheet.write(row_cursor, 6, row.status, fmt['text'])
            else:
                sheet.write(row_cursor, 0, row.code, fmt['text'])
                sheet.write_datetime(row_cursor, 1, row.applied.replace(tzinfo=None), fmt['date'])
                sheet.write(row_cursor, 2, row.status, fmt['text'])
                if row.score is None:
                    sheet.write(row_cursor, 3, '—', fmt['text'])
                else:
                    sheet.write_number(row_cursor, 3, row.score / 100, fmt['percent'])
                sheet.write(row_cursor, 4, row.evaluation, fmt['text'])
            row_cursor += 1

    return buffer.getvalue()
