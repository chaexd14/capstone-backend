import uuid
from time import perf_counter

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.accounts.models import UserRole
from apps.applications.models import Application, RecruiterTask
from apps.companies.models import Company
from apps.jobs.models import Job, JobStatus


class CollectionPaginationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Pagination Company')
        self.recruiter = User.objects.create_user(username=str(uuid.uuid4()), email='pager@example.test')
        self.recruiter.role = 'recruiter'
        UserRole.objects.create(user=self.recruiter, company=self.company, role='recruiter')
        self.client = APIClient()
        self.client.force_authenticate(self.recruiter)

        self.jobs = [
            Job.objects.create(
                company=self.company,
                title=f'Engineer {index:02d}',
                department='Engineering',
                description='Build dependable systems for the hiring platform.',
                required_skills=['Python'],
                status=JobStatus.DRAFT,
            )
            for index in range(25)
        ]
        self.applications = [
            Application.objects.create(
                job=self.jobs[0],
                first_name=f'Candidate{index}',
                last_name='Example',
                email=f'candidate{index}@example.test',
                user_id=uuid.uuid4(),
            )
            for index in range(25)
        ]

    def test_jobs_are_bounded_and_have_stable_envelope(self):
        response = self.client.get('/api/jobs/?status=DRAFT&page=1&page_size=1000')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 25)
        self.assertEqual(response.data['page_size'], 100)
        self.assertEqual(len(response.data['results']), 25)
        self.assertIn('status_counts', response.data['facets'])
        self.assertIn('applicant_count', response.data['results'][0])
        self.assertIn('top_match_score', response.data['results'][0])
        self.assertNotIn('description', response.data['results'][0])
        self.assertNotIn('rubric_weights', response.data['results'][0])

    def test_public_jobs_skip_recruiter_aggregates_and_support_revalidation(self):
        published = self.jobs[0]
        published.status = JobStatus.PUBLISHED
        published.save(update_fields=['status'])
        Application.objects.create(
            job=published,
            first_name='Public',
            last_name='Candidate',
            email='public@example.test',
            user_id=uuid.uuid4(),
        )
        anonymous = APIClient()

        with CaptureQueriesContext(connection) as queries:
            response = anonymous.get('/api/jobs/?page=1&page_size=20')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'][0]['applicant_count'], 0)
        self.assertIsNone(response.data['results'][0]['top_match_score'])
        self.assertIsNone(response.data['results'][0]['average_match_score'])
        self.assertIn('s-maxage=30', response['Cache-Control'])
        self.assertTrue(response['ETag'])
        collection_sql = ' '.join(query['sql'] for query in queries).lower()
        self.assertNotIn('applications_application', collection_sql)

        cached = anonymous.get(
            '/api/jobs/?page=1&page_size=20',
            HTTP_IF_NONE_MATCH=response['ETag'],
        )
        self.assertEqual(cached.status_code, 304)

        detail = anonymous.get(f'/api/jobs/{published.pk}/')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data['applicant_count'], 0)
        self.assertIn('s-maxage=30', detail['Cache-Control'])

    def test_application_pages_search_and_do_not_leak_other_companies(self):
        other_company = Company.objects.create(name='Other Company')
        other_job = Job.objects.create(
            company=other_company,
            title='Private Role',
            department='Private',
            description='This record belongs to another tenant.',
            required_skills=['Private'],
            status=JobStatus.DRAFT,
        )
        Application.objects.create(
            job=other_job,
            first_name='Hidden',
            last_name='Candidate',
            email='hidden@example.test',
            user_id=uuid.uuid4(),
        )

        first = self.client.get('/api/applications/?page=1&page_size=20')
        second = self.client.get('/api/applications/?page=2&page_size=20')
        searched = self.client.get('/api/applications/?q=Candidate24')
        self.assertEqual(first.data['count'], 25)
        self.assertEqual(len(first.data['results']), 20)
        self.assertEqual(len(second.data['results']), 5)
        self.assertEqual(searched.data['count'], 1)
        self.assertEqual(searched.data['results'][0]['first_name'], 'Candidate24')

    def test_tasks_keep_summary_while_paginating_results(self):
        for index, application in enumerate(self.applications):
            RecruiterTask.objects.create(
                application=application,
                title=f'Follow up {index}',
                created_by=str(self.recruiter.pk),
            )
        response = self.client.get('/api/applications/tasks/?status=open&page=1&page_size=20')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['summary']['open'], 25)
        self.assertEqual(response.data['count'], 25)
        self.assertEqual(len(response.data['results']), 20)

    def test_thousand_application_collection_is_bounded(self):
        Application.objects.bulk_create([
            Application(
                job=self.jobs[0],
                first_name=f'Benchmark{index}',
                last_name='Candidate',
                email=f'benchmark{index}@example.test',
                user_id=uuid.uuid4(),
            )
            for index in range(975)
        ])
        Job.objects.bulk_create([
            Job(
                company=self.company,
                title=f'Benchmark role {index}',
                department='Engineering',
                description='Benchmark description that must not be returned by list endpoints.',
                required_skills=['Python'],
                status=JobStatus.DRAFT,
            )
            for index in range(75)
        ])

        started = perf_counter()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get('/api/applications/?page=1&page_size=20')
        elapsed = perf_counter() - started

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1000)
        self.assertEqual(len(response.data['results']), 20)
        self.assertLess(len(response.content), 250 * 1024)
        self.assertLessEqual(len(queries), 15)
        self.assertLess(elapsed, 0.75)
