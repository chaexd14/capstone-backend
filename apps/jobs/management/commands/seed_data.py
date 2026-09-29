from django.core.management.base import BaseCommand
from apps.companies.models import Company
from apps.jobs.models import Job, JobStatus

class Command(BaseCommand):
    help = 'Seed multi-industry companies and benchmark job postings'

    def handle(self, *args, **kwargs):
        company, _ = Company.objects.get_or_create(
            name="The Boyz Technologies & Enterprises",
            defaults={
                "description": "Multi-industry enterprise and staffing conglomerate operating in Healthcare, BPO, Finance, Engineering, and Tech.",
                "website": "https://theboyz.tech"
            }
        )

        multi_industry_jobs = [
            {
                "title": "Registered Staff Nurse (ICU / General Ward)",
                "department": "Healthcare & Medical",
                "description": "We are seeking a dedicated Registered Nurse (RN) to deliver compassionate bedside care, perform patient triage, administer medications and IV therapy, monitor vitals, and maintain accurate clinical records.",
                "employment_type": "Full-time / Shifting",
                "location": "Quezon City / On-site Hospital",
                "minimum_experience": "1-2 years clinical experience",
                "education_requirement": "BS Nursing with active PRC Registered Nurse (RN) license",
                "required_skills": ["PRC Registered Nurse", "Patient Care", "IV Therapy", "Vital Signs Monitoring", "Medication Administration"],
                "preferred_skills": ["BLS/ACLS Certified", "ICU Care", "Triage"],
                "status": JobStatus.PUBLISHED,
            },
            {
                "title": "Senior Certified Public Accountant (CPA)",
                "department": "Finance & Accounting",
                "description": "Responsible for financial statement preparation, internal audit, monthly bank reconciliations, tax filings with the BIR, and general ledger oversight.",
                "employment_type": "Full-time",
                "location": "Makati / Hybrid",
                "minimum_experience": "2+ years accounting experience",
                "education_requirement": "BS in Accountancy with active PRC CPA License",
                "required_skills": ["PRC CPA", "Financial Reporting", "Tax Preparation", "General Ledger", "QuickBooks"],
                "preferred_skills": ["SAP", "BIR Compliance", "Auditing"],
                "status": JobStatus.PUBLISHED,
            },
            {
                "title": "Customer Service Representative (BPO / International Account)",
                "department": "BPO & Customer Support",
                "description": "Handle inbound customer calls, resolve billing and technical queries, manage tickets in Zendesk/Salesforce, and maintain high CSAT satisfaction metrics.",
                "employment_type": "Full-time / Night Shift",
                "location": "Pasig (Ortigas) / Hybrid",
                "minimum_experience": "1+ year BPO/Customer Support experience",
                "education_requirement": "College Graduate or Completed at least 2 years in College",
                "required_skills": ["Customer Service", "Inbound Calls", "English Fluency", "Troubleshooting", "Zendesk"],
                "preferred_skills": ["Salesforce", "Email Support", "Chat Support"],
                "status": JobStatus.PUBLISHED,
            },
            {
                "title": "Civil Site Project Engineer",
                "department": "Engineering & Construction",
                "description": "Supervise commercial building construction site activities, review structural drawings, manage subcontractor schedules, and perform project cost estimation and quality inspection.",
                "employment_type": "Full-time",
                "location": "Taguig (BGC) / Site",
                "minimum_experience": "2-3 years construction experience",
                "education_requirement": "BS Civil Engineering with PRC Civil Engineer License",
                "required_skills": ["PRC Civil Engineer", "AutoCAD", "Site Supervision", "Project Estimation", "QA/QC Inspection"],
                "preferred_skills": ["BOSH/COSH Safety Officer", "STAAD Pro", "Revit"],
                "status": JobStatus.PUBLISHED,
            },
            {
                "title": "B2B Corporate Sales & Account Executive",
                "department": "Sales & Business Development",
                "description": "Drive enterprise revenue by generating B2B leads, conducting executive pitches, negotiating corporate contracts, and managing sales pipelines through CRM.",
                "employment_type": "Full-time",
                "location": "Mandaluyong / Hybrid",
                "minimum_experience": "2+ years B2B Sales experience",
                "education_requirement": "BS in Business Administration, Marketing, or related field",
                "required_skills": ["B2B Sales", "Lead Generation", "Pipeline Management", "Negotiation", "CRM"],
                "preferred_skills": ["Cold Calling", "Salesforce", "Presentation Skills"],
                "status": JobStatus.PUBLISHED,
            },
            {
                "title": "Full Stack Python / React Developer",
                "department": "Information Technology",
                "description": "Build high-throughput web applications and AI workflows using Python, Django, PostgreSQL, and React/Next.js.",
                "employment_type": "Full-time",
                "location": "Manila / Remote",
                "minimum_experience": "2+ years",
                "education_requirement": "BS Computer Science, Information Technology, or relevant experience",
                "required_skills": ["Python", "Django", "PostgreSQL", "JavaScript", "React"],
                "preferred_skills": ["Docker", "Redis", "Next.js", "AWS"],
                "status": JobStatus.PUBLISHED,
            }
        ]

        created_count = 0
        for data in multi_industry_jobs:
            job, created = Job.objects.get_or_create(
                title=data["title"],
                company=company,
                defaults=data
            )
            if created:
                created_count += 1
            else:
                # Update with universal data
                for key, val in data.items():
                    setattr(job, key, val)
                job.save()

        self.stdout.write(self.style.SUCCESS(f"Successfully seeded/updated {len(multi_industry_jobs)} multi-industry jobs."))
