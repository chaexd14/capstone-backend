from datetime import datetime, timedelta, timezone
from io import BytesIO
from zipfile import ZipFile

import pymupdf
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import SecurityEvent, UserRole
from apps.applications.models import Application, ApplicationStatus
from apps.companies.models import Company
from apps.jobs.models import Job, JobStatus
from apps.resumes.models import MatchResult, Resume


class AllApplicationsReportTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Acme Corp')
        self.other_company = Company.objects.create(name='Other Corp')

        self.job_eng = Job.objects.create(
            company=self.company,
            title='Backend Engineer',
            department='Engineering',
            required_skills=['Python'],
        )
        self.job_ops = Job.objects.create(
            company=self.company,
            title='Operations Lead',
            department='Operations',
            required_skills=['Logistics'],
        )
        self.archived_job = Job.objects.create(
            company=self.company,
            title='Legacy Role',
            department='Operations',
            status=JobStatus.ARCHIVED,
        )
        self.foreign_job = Job.objects.create(
            company=self.other_company,
            title='Foreign Role',
            department='Design',
        )

        self.recruiter = User.objects.create_user(username='recruiter')
        self.recruiter.role = 'recruiter'
        UserRole.objects.create(user=self.recruiter, company=self.company, role='recruiter')

        self.strong_app = Application.objects.create(
            job=self.job_eng,
            first_name='Jane',
            last_name='Reyes',
            email='jane@example.test',
            phone='09171234567',
            status=ApplicationStatus.SHORTLISTED,
        )
        Resume.objects.create(application=self.strong_app, processing_status='COMPLETED')
        MatchResult.objects.create(application=self.strong_app, match_score=89.5)

        self.weak_app = Application.objects.create(
            job=self.job_ops,
            first_name='Carlos',
            last_name='Santos',
            email='carlos@example.test',
            phone='09189990000',
            status=ApplicationStatus.UNDER_REVIEW,
        )
        Resume.objects.create(application=self.weak_app, processing_status='COMPLETED')
        MatchResult.objects.create(application=self.weak_app, match_score=42.0)

        self.pending_app = Application.objects.create(
            job=self.job_eng,
            first_name='Unscored',
            last_name='Candidate',
            email='unscored@example.test',
            status=ApplicationStatus.SUBMITTED,
        )

        self.archived_app = Application.objects.create(
            job=self.archived_job,
            first_name='Archived',
            last_name='Person',
            email='archived@example.test',
            status=ApplicationStatus.SUBMITTED,
        )
        self.foreign_app = Application.objects.create(
            job=self.foreign_job,
            first_name='Foreign',
            last_name='Person',
            email='foreign@example.test',
            status=ApplicationStatus.SHORTLISTED,
        )

        for app, day in (
            (self.strong_app, 3),
            (self.weak_app, 4),
            (self.pending_app, 4),
            (self.archived_app, 3),
            (self.foreign_app, 3),
        ):
            Application.objects.filter(pk=app.pk).update(
                applied_at=datetime(2026, 1, day, 12, 0, 0, tzinfo=timezone.utc)
            )

        self.client = APIClient()
        self.url = '/api/applications/report/'

    def export(self, payload):
        return self.client.post(self.url, payload, format='json')

    def test_permissions(self):
        self.assertEqual(self.export({'file_type': 'pdf'}).status_code, 401)
        applicant = User.objects.create_user(username='applicant')
        self.client.force_authenticate(applicant)
        self.assertEqual(self.export({'file_type': 'xlsx'}).status_code, 403)

    def test_pdf_and_excel_reveal_full_identity_and_scope_to_company(self):
        self.client.force_authenticate(self.recruiter)
        pdf = self.export({'file_type': 'pdf', 'date_from': '2026-01-03', 'date_to': '2026-01-04'})
        excel = self.export({'file_type': 'xlsx', 'date_from': '2026-01-03', 'date_to': '2026-01-04'})

        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(excel.status_code, 200)
        self.assertIn('private, no-store', pdf['Cache-Control'])
        self.assertIn('private, no-store', excel['Cache-Control'])

        with pymupdf.open(stream=pdf.content, filetype='pdf') as document:
            pdf_text = ' '.join(page.get_text() for page in document)
        with ZipFile(BytesIO(excel.content)) as workbook:
            excel_text = workbook.read('xl/sharedStrings.xml').decode()
            worksheet_xml = workbook.read('xl/worksheets/sheet1.xml').decode()
            styles_xml = workbook.read('xl/styles.xml').decode()
            self.assertNotIn('xl/worksheets/sheet2.xml', workbook.namelist())
            self.assertNotIn('showGridLines="0"', worksheet_xml)
            self.assertIn('<left style="thin">', styles_xml)
            self.assertIn('<v>0.895</v>', worksheet_xml)
            self.assertIn('<autoFilter', worksheet_xml)

        for content in (pdf_text, excel_text):
            self.assertIn('Applicant #', content)
            self.assertIn('Candidate', content)
            self.assertIn('Email', content)
            self.assertIn('Phone', content)
            self.assertIn('Date Applied', content)
            self.assertIn(self.strong_app.candidate_code, content)
            self.assertIn('Jane Reyes', content)
            self.assertIn('jane@example.test', content)
            self.assertIn('09171234567', content)
            self.assertIn('Backend Engineer', content)
            self.assertIn('Carlos Santos', content)
            self.assertNotIn('Foreign Person', content)
            self.assertNotIn('Archived Person', content)

        self.assertIn('89.5%', pdf_text)
        self.assertEqual(SecurityEvent.objects.filter(action='application.report_export').count(), 2)

    def test_filters_by_status_match_band_job_and_date(self):
        self.client.force_authenticate(self.recruiter)

        strong_only = self.export({'file_type': 'xlsx', 'match_band': 'STRONG'})
        self.assertEqual(strong_only.status_code, 200)
        with ZipFile(BytesIO(strong_only.content)) as workbook:
            text = workbook.read('xl/sharedStrings.xml').decode()
        self.assertIn('Jane Reyes', text)
        self.assertNotIn('Carlos Santos', text)
        self.assertNotIn('Unscored Candidate', text)

        weak_only = self.export({'file_type': 'xlsx', 'match_band': 'WEAK'})
        self.assertEqual(weak_only.status_code, 200)
        with ZipFile(BytesIO(weak_only.content)) as workbook:
            text = workbook.read('xl/sharedStrings.xml').decode()
        self.assertIn('Carlos Santos', text)
        self.assertNotIn('Jane Reyes', text)
        self.assertNotIn('Unscored Candidate', text)

        by_job_and_status = self.export({
            'file_type': 'xlsx',
            'job_id': self.job_eng.pk,
            'status': ApplicationStatus.SUBMITTED,
            'date_from': '2026-01-04',
            'date_to': '2026-01-04',
        })
        self.assertEqual(by_job_and_status.status_code, 200)
        with ZipFile(BytesIO(by_job_and_status.content)) as workbook:
            text = workbook.read('xl/sharedStrings.xml').decode()
        self.assertIn('Unscored Candidate', text)
        self.assertNotIn('Jane Reyes', text)

    def test_validation_errors_and_foreign_job_isolation(self):
        self.client.force_authenticate(self.recruiter)

        bad_dates = self.export({'file_type': 'pdf', 'date_from': '2026-01-05', 'date_to': '2026-01-03'})
        self.assertEqual(bad_dates.status_code, 400)

        tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
        future_date = self.export({'file_type': 'pdf', 'date_to': tomorrow})
        self.assertEqual(future_date.status_code, 400)

        empty_match = self.export({'file_type': 'pdf', 'status': ApplicationStatus.HIRED})
        self.assertEqual(empty_match.status_code, 400)

        foreign_job = self.export({'file_type': 'pdf', 'job_id': self.foreign_job.pk})
        self.assertEqual(foreign_job.status_code, 400)

