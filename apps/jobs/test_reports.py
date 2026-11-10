from datetime import datetime, timezone
from io import BytesIO
from zipfile import ZipFile

import pymupdf
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import SecurityEvent, UserRole
from apps.applications.models import Application, ApplicationStatus
from apps.companies.models import Company
from apps.jobs.models import Job
from apps.resumes.models import MatchResult, Resume


class JobReportTests(TestCase):
    def setUp(self):
        company = Company.objects.create(name='Test company')
        self.job = Job.objects.create(
            company=company, title='QA Engineer', department='Engineering',
            required_skills=['Python', 'Selenium'],
        )
        self.recruiter = User.objects.create_user(username='recruiter')
        self.recruiter.role = 'recruiter'
        UserRole.objects.create(user=self.recruiter, company=company, role='recruiter')
        self.application = Application.objects.create(
            job=self.job, first_name='Synthetic', last_name='Private', email='private@example.test',
            phone='09171234567', notes='Confidential recruiter notes', status=ApplicationStatus.SHORTLISTED,
        )
        evidence = 'Built Python regression suites and maintained QA automation.'
        Resume.objects.create(
            application=self.application, processing_status='COMPLETED',
            extracted_text='Private original text', redacted_text=evidence,
        )
        MatchResult.objects.create(
            application=self.application, match_score=89.5, skill_match_score=94,
            matched_skills=['Python'], missing_skills=['Selenium'], ai_insights={
                'must_have_breakdown': [{'skill': 'Python', 'status': 'met', 'evidence': evidence}],
                'other_evidence': ['private@example.test'],
            },
        )
        self.pending = Application.objects.create(
            job=self.job, first_name='Pending', last_name='Applicant', email='pending@example.test',
        )
        self.old = Application.objects.create(job=self.job, email='excluded@example.test')
        for app, day in ((self.application, 3), (self.pending, 3), (self.old, 2)):
            Application.objects.filter(pk=app.pk).update(
                applied_at=datetime(2026, 1, day, 23, 59, 59, tzinfo=timezone.utc)
            )
        self.client = APIClient()
        self.url = f'/api/jobs/{self.job.pk}/report/'

    def export(self, file_type):
        return self.client.post(self.url, {
            'file_type': file_type, 'date_from': '2026-01-03', 'date_to': '2026-01-03',
        }, format='json')

    def test_export_respects_recruiter_and_job_permissions(self):
        self.assertEqual(self.export('pdf').status_code, 401)
        self.client.force_authenticate(User.objects.create_user(username='applicant'))
        self.assertEqual(self.export('xlsx').status_code, 403)
        self.recruiter.role_profile.company = Company.objects.create(name='Different company')
        self.recruiter.role_profile.save(update_fields=['company'])
        self.client.force_authenticate(self.recruiter)
        self.assertEqual(self.export('pdf').status_code, 404)

    def test_pdf_and_excel_use_the_filtered_anonymized_pipeline(self):
        self.client.force_authenticate(self.recruiter)
        pdf = self.export('pdf')
        excel = self.export('xlsx')
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(excel.status_code, 200)
        self.assertIn('private, no-store', pdf['Cache-Control'])
        self.assertIn('private, no-store', excel['Cache-Control'])
        with pymupdf.open(stream=pdf.content, filetype='pdf') as document:
            pdf_text = ' '.join(page.get_text() for page in document)
        with ZipFile(BytesIO(excel.content)) as workbook:
            excel_text = workbook.read('xl/sharedStrings.xml').decode()
            self.assertIn('89.5%', pdf_text)
            self.assertIn('<v>0.895</v>', workbook.read('xl/worksheets/sheet2.xml').decode())
            self.assertIn('<autoFilter', workbook.read('xl/worksheets/sheet2.xml').decode())
            self.assertIn('Pending', excel_text)
        for content in (pdf_text, excel_text):
            self.assertIn(self.application.candidate_code, content)
            self.assertIn('Built Python regression suites', content)
            self.assertIn('Selenium', content)
            self.assertNotIn(self.old.candidate_code, content)
            for private in ('Synthetic Private', 'private@example.test', '09171234567',
                            'Private original text', 'Confidential recruiter notes'):
                self.assertNotIn(private, content)
        self.assertIn(self.pending.candidate_code, excel_text)
        self.assertEqual(SecurityEvent.objects.filter(action='job.report_export').count(), 2)

    def test_invalid_date_order_returns_validation_error(self):
        self.client.force_authenticate(self.recruiter)
        response = self.client.post(self.url, {
            'file_type': 'pdf', 'date_from': '2026-01-04', 'date_to': '2026-01-03',
        }, format='json')
        self.assertEqual(response.status_code, 400)
