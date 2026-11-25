from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.companies.models import Company
from apps.jobs.models import ArchivedJob, Job, JobStatus
from apps.jobs.serializers import JobSerializer
from apps.jobs.services import lint_job_requisition


MULTI_INDUSTRY_JOBS = [
    # =========================================================================
    # 1. Information Technology (3 roles)
    # =========================================================================
    {
        "title": "Junior Full-Stack Software Engineer",
        "department": "Information Technology",
        "employment_type": "Full-time",
        "location": "Manila / Remote",
        "minimum_experience": "0-1 year",
        "education_requirement": "BS in Computer Science, Information Technology, or equivalent practical software development experience",
        "salary_min": 35000,
        "salary_max": 50000,
        "job_overview": (
            "Join our product engineering squad to build and maintain responsive web applications and RESTful APIs "
            "using TypeScript, React, and Python. Ideal for early-career developers who want structured code reviews "
            "and hands-on production ownership."
        ),
        "description": (
            "• Build and maintain responsive frontend features using React, Next.js, TypeScript, and Tailwind CSS.\n"
            "• Implement backend API endpoints, database models, and automated tests in Python and Django.\n"
            "• Write clean, maintainable SQL queries and optimize PostgreSQL schema migrations.\n"
            "• Collaborate in sprint planning, pull request reviews, and bug triage with senior engineers.\n"
            "• Document technical specifications, API contracts, and deployment steps."
        ),
        "required_skills": [
            "Python",
            "JavaScript / TypeScript",
            "React",
            "SQL / PostgreSQL",
            "REST API Development",
            "Git Version Control",
        ],
        "preferred_skills": [
            "Django",
            "Next.js",
            "Docker",
            "Tailwind CSS",
            "Automated Unit Testing",
        ],
        "rubric_weights": {
            "required_skills": 40,
            "experience": 15,
            "education": 15,
            "preferred_skills": 10,
            "projects": 20,
        },
        "status": JobStatus.PUBLISHED,
    },
    {
        "title": "Cloud & DevOps Engineer",
        "department": "Information Technology",
        "employment_type": "Contract",
        "location": "Makati City / Hybrid",
        "minimum_experience": "3-5 years",
        "education_requirement": "BS in Computer Science, Information Technology, Computer Engineering, or equivalent cloud infrastructure experience",
        "salary_min": 85000,
        "salary_max": 125000,
        "job_overview": (
            "Architect and automate cloud infrastructure, CI/CD release pipelines, and observability stacks across "
            "multi-environment workloads. You will partner with application teams to improve deployment reliability, "
            "security posture, and cloud cost efficiency."
        ),
        "description": (
            "• Provision and manage cloud infrastructure as code using Terraform across AWS and container platforms.\n"
            "• Design, harden, and maintain automated CI/CD pipelines with GitHub Actions and Docker.\n"
            "• Configure centralized logging, metrics, alerting, and incident runbooks for production services.\n"
            "• Enforce least-privilege IAM policies, secrets management, and automated backup recovery drills.\n"
            "• Conduct capacity planning and cloud spend optimization reviews with engineering leadership."
        ),
        "required_skills": [
            "AWS Cloud Infrastructure",
            "Docker & Containerization",
            "CI/CD Pipelines (GitHub Actions)",
            "Terraform (Infrastructure as Code)",
            "Linux System Administration",
            "Monitoring & Observability",
        ],
        "preferred_skills": [
            "Kubernetes",
            "AWS Certified Solutions Architect",
            "PostgreSQL Administration",
            "Python / Bash Scripting",
            "Cloudflare",
        ],
        "rubric_weights": {
            "required_skills": 40,
            "experience": 25,
            "education": 10,
            "preferred_skills": 15,
            "projects": 10,
        },
        "status": JobStatus.PUBLISHED,
    },
    {
        "title": "Senior Cybersecurity & Network Engineer",
        "department": "Information Technology",
        "employment_type": "Full-time",
        "location": "Taguig (BGC) / Hybrid",
        "minimum_experience": "5+ years",
        "education_requirement": "BS in Information Technology, Computer Science, Cybersecurity, or related technical discipline",
        "salary_min": 110000,
        "salary_max": 145000,
        "job_overview": (
            "Lead enterprise network design, zero-trust security controls, and vulnerability governance across "
            "hybrid cloud and corporate environments. You will set technical security standards and guide incident "
            "response readiness."
        ),
        "description": (
            "• Design segmented enterprise LAN/WAN/VPN topologies, next-generation firewalls, and zero-trust access controls.\n"
            "• Lead vulnerability assessments, penetration test remediation, and SIEM threat detection tuning.\n"
            "• Establish ISO 27001 and Data Privacy Act technical compliance controls across applications and infrastructure.\n"
            "• Direct security incident response investigations, root-cause analyses, and recovery playbooks.\n"
            "• Mentor network and systems administrators on secure configuration baselines and hardening standards."
        ),
        "required_skills": [
            "Enterprise Network Architecture",
            "Firewall & VPN Administration",
            "Vulnerability Management & Penetration Testing",
            "SIEM & Incident Response",
            "Zero-Trust Security",
            "ISO 27001 / Compliance Controls",
        ],
        "preferred_skills": [
            "CISSP or CISM Certified",
            "Cisco CCNP / CCNA",
            "Cloud Security (AWS / Azure)",
            "Python Security Automation",
        ],
        "rubric_weights": {
            "required_skills": 40,
            "experience": 25,
            "education": 15,
            "preferred_skills": 10,
            "projects": 10,
        },
        "status": JobStatus.PUBLISHED,
    },
    # =========================================================================
    # 2. Finance & Accounting (3 roles)
    # =========================================================================
    {
        "title": "Junior Bookkeeper & Tax Associate",
        "department": "Finance & Accounting",
        "employment_type": "Part-time",
        "location": "Quezon City / Hybrid",
        "minimum_experience": "0-1 year",
        "education_requirement": "BS in Accountancy, Management Accounting, Accounting Technology, or Finance",
        "salary_min": 22000,
        "salary_max": 32000,
        "job_overview": (
            "Support daily bookkeeping, accounts payable and receivable recording, and monthly statutory tax "
            "schedules on a flexible part-time schedule. Great fit for accounting professionals building hands-on "
            "BIR compliance and cloud ledger proficiency."
        ),
        "description": (
            "• Record daily sales invoices, vendor bills, disbursements, and journal entries in cloud accounting software.\n"
            "• Perform monthly bank and petty cash reconciliations and resolve ledger discrepancies promptly.\n"
            "• Prepare supporting schedules for monthly and quarterly BIR withholding tax and VAT filings.\n"
            "• Organize official receipts, vouchers, and audit-ready financial documentation.\n"
            "• Assist senior accountants with month-end closing checklists and aging reports."
        ),
        "required_skills": [
            "Bookkeeping & Journal Entries",
            "Accounts Payable & Receivable",
            "Bank Reconciliation",
            "BIR Tax Compliance Basics",
            "Microsoft Excel",
            "QuickBooks or Xero",
        ],
        "preferred_skills": [
            "eBIRForms / eFPS",
            "Payroll Processing",
            "Google Sheets Automation",
            "SAP Business One",
        ],
        "rubric_weights": {
            "required_skills": 35,
            "experience": 20,
            "education": 25,
            "preferred_skills": 10,
            "projects": 10,
        },
        "status": JobStatus.PUBLISHED,
    },
    {
        "title": "Certified Public Accountant (CPA) – General Ledger",
        "department": "Finance & Accounting",
        "employment_type": "Full-time",
        "location": "Makati City / Hybrid",
        "minimum_experience": "2-3 years",
        "education_requirement": "BS in Accountancy with active PRC Certified Public Accountant (CPA) License",
        "salary_min": 55000,
        "salary_max": 80000,
        "job_overview": (
            "Own general ledger integrity, PFRS-compliant financial statement preparation, and statutory BIR tax "
            "reporting for corporate entities. You will coordinate external audits and strengthen internal financial "
            "controls."
        ),
        "description": (
            "• Prepare monthly, quarterly, and annual financial statements in accordance with PFRS/IFRS standards.\n"
            "• Review general ledger reconciliations, accruals, fixed asset schedules, and intercompany eliminations.\n"
            "• Oversee accurate computation and timely filing of BIR returns (1601-C, 0619-E, 2550Q, 1702).\n"
            "• Lead external audit readiness, prepare audit schedules, and address auditor inquiries.\n"
            "• Enforce internal accounting controls and document standard operating procedures."
        ),
        "required_skills": [
            "PRC CPA",
            "Financial Reporting (PFRS / IFRS)",
            "General Ledger Accounting",
            "BIR Tax Preparation & Compliance",
            "External Audit Coordination",
            "ERP Accounting Systems",
        ],
        "preferred_skills": [
            "SAP S/4HANA or NetSuite",
            "QuickBooks Online",
            "Variance Analysis",
            "Internal Controls Design",
        ],
        "rubric_weights": {
            "required_skills": 30,
            "experience": 25,
            "education": 25,
            "preferred_skills": 10,
            "projects": 10,
        },
        "status": JobStatus.PUBLISHED,
    },
    {
        "title": "Senior Financial Planning & Analysis (FP&A) Manager",
        "department": "Finance & Accounting",
        "employment_type": "Full-time",
        "location": "Taguig (BGC) / Hybrid",
        "minimum_experience": "5+ years",
        "education_requirement": "BS in Accountancy, Finance, or Economics; active PRC CPA or CFA charter strongly preferred",
        "salary_min": 105000,
        "salary_max": 140000,
        "job_overview": (
            "Drive multi-year financial modeling, annual operating budgets, and executive board reporting across "
            "business units. You will translate financial performance data into actionable margin, cash flow, and "
            "capital allocation recommendations."
        ),
        "description": (
            "• Lead annual budgeting, quarterly rolling forecasts, and three-statement financial modeling.\n"
            "• Deliver monthly management reporting packs covering P&L variance, unit economics, and cash flow.\n"
            "• Partner with department heads to evaluate pricing strategies, headcount plans, and ROI business cases.\n"
            "• Build automated executive KPI dashboards in Power BI or Tableau linked to ERP data.\n"
            "• Present financial risk assessments and scenario analyses to senior leadership."
        ),
        "required_skills": [
            "Financial Modeling & Forecasting",
            "Budgeting & Variance Analysis",
            "Executive Financial Reporting",
            "Cash Flow & Working Capital Analysis",
            "Advanced Microsoft Excel",
            "PRC CPA or Financial Analysis Certification",
        ],
        "preferred_skills": [
            "Power BI / Tableau",
            "SQL for Financial Data",
            "SAP / Oracle NetSuite",
            "CFA Level II / III",
        ],
        "rubric_weights": {
            "required_skills": 30,
            "experience": 30,
            "education": 20,
            "preferred_skills": 10,
            "projects": 10,
        },
        "status": JobStatus.PUBLISHED,
    },
    # =========================================================================
    # 3. Engineering & Construction (3 roles)
    # =========================================================================
    {
        "title": "Civil Engineering Intern (Site & CAD Support)",
        "department": "Engineering & Construction",
        "employment_type": "Internship",
        "location": "Mandaluyong / On-site",
        "minimum_experience": "0-1 year",
        "education_requirement": "Currently pursuing or completed BS in Civil Engineering",
        "salary_min": 18000,
        "salary_max": 22000,
        "job_overview": (
            "Gain practical commercial construction exposure supporting CAD drafting, quantity takeoffs, and daily "
            "site progress documentation alongside licensed project supervisors. Designed for aspiring structural "
            "and construction professionals."
        ),
        "description": (
            "• Assist in updating 2D architectural and structural shop drawings using AutoCAD.\n"
            "• Support quantity takeoffs, bill of quantities (BOQ) checks, and material delivery logs.\n"
            "• Compile daily site progress reports, photo documentation, and inspection checklists.\n"
            "• Coordinate drawing transmittals and RFI logs between office planners and field teams.\n"
            "• Follow all on-site occupational safety guidelines and quality assurance procedures."
        ),
        "required_skills": [
            "AutoCAD 2D Drafting",
            "Blueprint & Plan Reading",
            "Quantity Takeoff Basics",
            "Microsoft Excel",
            "Construction Documentation",
        ],
        "preferred_skills": [
            "SketchUp",
            "Revit / BIM Basics",
            "MS Project",
            "STAAD Pro Familiarity",
        ],
        "rubric_weights": {
            "required_skills": 30,
            "experience": 10,
            "education": 35,
            "preferred_skills": 10,
            "projects": 15,
        },
        "status": JobStatus.PUBLISHED,
    },
    {
        "title": "Licensed Civil Site Project Engineer",
        "department": "Engineering & Construction",
        "employment_type": "Full-time",
        "location": "Taguig (BGC) / Site-based",
        "minimum_experience": "2-3 years",
        "education_requirement": "BS in Civil Engineering with active PRC Civil Engineer License",
        "salary_min": 48000,
        "salary_max": 72000,
        "job_overview": (
            "Supervise day-to-day commercial building construction operations, structural quality inspections, and "
            "subcontractor scheduling on site. You will ensure every milestone is delivered safely, on schedule, and "
            "to engineering specification."
        ),
        "description": (
            "• Supervise structural concrete pouring, rebar installation, masonry, and finishing works on site.\n"
            "• Review structural blueprints, shop drawings, and method statements for technical compliance.\n"
            "• Conduct QA/QC inspections, material testing verification, and punch-list rectification.\n"
            "• Prepare project cost estimates, progress billings, and weekly S-curve schedule updates.\n"
            "• Coordinate subcontractors and enforce DOLE construction safety standards on site."
        ),
        "required_skills": [
            "PRC Civil Engineer",
            "AutoCAD",
            "Site Supervision",
            "QA/QC Inspection",
            "Project Cost Estimation & BOQ",
            "Construction Scheduling",
        ],
        "preferred_skills": [
            "BOSH / COSH Safety Officer",
            "STAAD Pro",
            "Revit / BIM",
            "MS Project / Primavera P6",
        ],
        "rubric_weights": {
            "required_skills": 30,
            "experience": 25,
            "education": 25,
            "preferred_skills": 10,
            "projects": 10,
        },
        "status": JobStatus.PUBLISHED,
    },
    {
        "title": "Senior Registered Electrical Engineer (MEPFS)",
        "department": "Engineering & Construction",
        "employment_type": "Contract",
        "location": "Cebu City / Site-based",
        "minimum_experience": "5+ years",
        "education_requirement": "BS in Electrical Engineering with active PRC Registered Electrical Engineer (REE) License",
        "salary_min": 80000,
        "salary_max": 115000,
        "job_overview": (
            "Lead electrical design review, high-voltage power distribution, and MEPFS commissioning for large-scale "
            "commercial and industrial facilities in Cebu. You will oversee testing, PEC compliance, and handover "
            "sign-offs."
        ),
        "description": (
            "• Review and approve electrical load schedules, single-line diagrams, lighting layouts, and auxiliary systems.\n"
            "• Oversee installation, testing, and commissioning of switchgear, transformers, and backup generators.\n"
            "• Ensure full compliance with the Philippine Electrical Code (PEC) and local utility requirements.\n"
            "• Coordinate MEPFS clash resolution with mechanical, plumbing, and structural project teams.\n"
            "• Evaluate contractor variation orders, technical submittals, and final as-built documentation."
        ),
        "required_skills": [
            "PRC Registered Electrical Engineer",
            "Philippine Electrical Code (PEC)",
            "Power Distribution & Load Analysis",
            "MEPFS Coordination",
            "Testing & Commissioning",
            "AutoCAD Electrical",
        ],
        "preferred_skills": [
            "Revit MEP",
            "ETAP / SKM PowerTools",
            "COSH / Safety Officer Certification",
            "Solar PV Systems",
        ],
        "rubric_weights": {
            "required_skills": 30,
            "experience": 30,
            "education": 25,
            "preferred_skills": 5,
            "projects": 10,
        },
        "status": JobStatus.PUBLISHED,
    },
    # =========================================================================
    # 4. Sales & Business Development (3 roles)
    # =========================================================================
    {
        "title": "Sales Development Representative (SDR)",
        "department": "Sales & Business Development",
        "employment_type": "Part-time",
        "location": "Manila / Remote",
        "minimum_experience": "0-1 year",
        "education_requirement": "Bachelor's degree in Business Administration, Marketing, Communications, or equivalent sales experience",
        "salary_min": 25000,
        "salary_max": 38000,
        "job_overview": (
            "Generate and qualify B2B sales pipeline through outbound prospecting, discovery outreach, and CRM "
            "campaign execution in a flexible remote part-time role. You will book qualified executive demos for "
            "Account Executives."
        ),
        "description": (
            "• Research target B2B accounts and identify key decision-makers using LinkedIn and prospecting databases.\n"
            "• Execute multi-channel outbound email, phone, and social outreach sequences to generate meetings.\n"
            "• Conduct structured discovery calls to qualify prospect needs, timelines, and budget fit.\n"
            "• Maintain accurate lead activity, call notes, and pipeline stages inside HubSpot or Salesforce.\n"
            "• Partner with marketing and Account Executives to refine messaging and improve conversion rates."
        ),
        "required_skills": [
            "Lead Generation & Prospecting",
            "Outbound Cold Calling & Email",
            "Discovery & Qualification",
            "CRM Management (HubSpot / Salesforce)",
            "Written & Verbal Communication",
        ],
        "preferred_skills": [
            "LinkedIn Sales Navigator",
            "Apollo.io / ZoomInfo",
            "B2B SaaS Exposure",
            "Pipeline Reporting",
        ],
        "rubric_weights": {
            "required_skills": 35,
            "experience": 25,
            "education": 10,
            "preferred_skills": 15,
            "projects": 15,
        },
        "status": JobStatus.PUBLISHED,
    },
    {
        "title": "B2B Corporate Account Executive",
        "department": "Sales & Business Development",
        "employment_type": "Full-time",
        "location": "Mandaluyong / Hybrid",
        "minimum_experience": "2-3 years",
        "education_requirement": "BS in Business Administration, Marketing, Economics, or related commercial field",
        "salary_min": 55000,
        "salary_max": 85000,
        "job_overview": (
            "Own the full B2B sales cycle from solution discovery and executive product demonstrations to contract "
            "negotiation and closing. You will build long-term mid-market and corporate client relationships across "
            "Metro Manila."
        ),
        "description": (
            "• Manage end-to-end B2B deal cycles from initial qualification through proposal presentation and closing.\n"
            "• Deliver tailored product demonstrations and ROI proposals aligned with client business objectives.\n"
            "• Negotiate commercial terms, service-level agreements, and multi-year subscription contracts.\n"
            "• Maintain accurate quarterly revenue forecasts and deal hygiene inside Salesforce CRM.\n"
            "• Coordinate seamless post-sale handoffs with onboarding and customer success teams."
        ),
        "required_skills": [
            "B2B Consultative Sales",
            "Pipeline Management & Forecasting",
            "Contract Negotiation",
            "Executive Presentations",
            "Salesforce / HubSpot CRM",
            "Account Management",
        ],
        "preferred_skills": [
            "MEDDIC / SPIN Selling",
            "Enterprise SaaS Sales",
            "Proposal & RFP Writing",
            "Client Retention & Upselling",
        ],
        "rubric_weights": {
            "required_skills": 35,
            "experience": 30,
            "education": 10,
            "preferred_skills": 10,
            "projects": 15,
        },
        "status": JobStatus.PUBLISHED,
    },
    {
        "title": "Enterprise Partnerships & Revenue Director",
        "department": "Sales & Business Development",
        "employment_type": "Full-time",
        "location": "Makati City / Hybrid",
        "minimum_experience": "5+ years",
        "education_requirement": "Bachelor's degree in Business, Management, Marketing, or related field; MBA is an advantage",
        "salary_min": 100000,
        "salary_max": 140000,
        "job_overview": (
            "Spearhead strategic enterprise alliances, channel partner programs, and high-value corporate revenue "
            "expansion across the Philippines and Southeast Asia. You will lead senior commercial negotiations and "
            "coach account teams."
        ),
        "description": (
            "• Develop and execute annual enterprise revenue and channel partnership growth strategies.\n"
            "• Structure and negotiate high-value commercial agreements, reseller partnerships, and joint GTM plans.\n"
            "• Lead C-suite executive briefings, strategic account planning, and multi-stakeholder enterprise deals.\n"
            "• Coach and mentor Account Executives and SDRs on quota attainment and pipeline conversion.\n"
            "• Track CAC, ARR expansion, and partner-sourced revenue metrics for executive leadership reviews."
        ),
        "required_skills": [
            "Enterprise Sales Leadership",
            "Strategic Partnerships & Alliances",
            "Revenue Strategy & GTM Planning",
            "C-Suite Negotiation",
            "Sales Team Coaching & Quota Management",
            "Commercial Contracting",
        ],
        "preferred_skills": [
            "Regional SEA Market Expansion",
            "SaaS / Tech Enterprise Sales",
            "Salesforce Analytics",
            "MBA",
        ],
        "rubric_weights": {
            "required_skills": 30,
            "experience": 35,
            "education": 10,
            "preferred_skills": 10,
            "projects": 15,
        },
        "status": JobStatus.PUBLISHED,
    },
    # =========================================================================
    # 5. Healthcare & Medical (3 roles)
    # =========================================================================
    {
        "title": "Registered Staff Nurse (ICU / General Ward)",
        "department": "Healthcare & Medical",
        "employment_type": "Shifting",
        "location": "Quezon City / On-site",
        "minimum_experience": "1-2 years",
        "education_requirement": "BS in Nursing with active PRC Registered Nurse (RN) License",
        "salary_min": 38000,
        "salary_max": 55000,
        "job_overview": (
            "Deliver safe, evidence-based bedside nursing care across general ward and critical care units on a "
            "rotational hospital shift schedule. You will collaborate closely with attending physicians to monitor "
            "patient recovery and clinical safety."
        ),
        "description": (
            "• Perform comprehensive patient assessments, vital signs monitoring, and clinical triage on assigned shifts.\n"
            "• Administer prescribed oral, intramuscular, and intravenous (IV) medications following safety protocols.\n"
            "• Maintain accurate, timely nursing notes and medication administration records in EHR systems.\n"
            "• Coordinate with physicians, pharmacists, and allied health teams to execute individualized care plans.\n"
            "• Educate patients and families on discharge instructions, infection control, and home care."
        ),
        "required_skills": [
            "PRC Registered Nurse",
            "Bedside Patient Care",
            "IV Therapy & Medication Administration",
            "Vital Signs Monitoring & Triage",
            "Clinical Documentation (EHR)",
            "Infection Control Protocols",
        ],
        "preferred_skills": [
            "BLS / ACLS Certified",
            "ICU / Critical Care Experience",
            "IVT Certification",
            "Wound Care Management",
        ],
        "rubric_weights": {
            "required_skills": 30,
            "experience": 25,
            "education": 25,
            "preferred_skills": 10,
            "projects": 10,
        },
        "status": JobStatus.PUBLISHED,
    },
    {
        "title": "Clinical Pharmacist (Hospital Operations)",
        "department": "Healthcare & Medical",
        "employment_type": "Full-time",
        "location": "Makati City / On-site",
        "minimum_experience": "2-3 years",
        "education_requirement": "BS in Pharmacy with active PRC Registered Pharmacist (RPh) License",
        "salary_min": 45000,
        "salary_max": 65000,
        "job_overview": (
            "Oversee inpatient and outpatient medication dispensing, drug-interaction reviews, and FDA/DOH "
            "regulatory compliance within hospital pharmacy operations. You will partner with clinicians to optimize "
            "safe therapeutic outcomes."
        ),
        "description": (
            "• Review physician medication orders for dosage accuracy, contraindications, and drug interactions.\n"
            "• Dispense inpatient and outpatient prescriptions while ensuring strict controlled-substance accountability.\n"
            "• Counsel patients and clinical staff on proper medication usage, side effects, and storage.\n"
            "• Manage pharmaceutical inventory levels, cold-chain monitoring, and expiry audits.\n"
            "• Maintain full compliance with FDA Philippines, DOH, and Dangerous Drugs Board regulations."
        ),
        "required_skills": [
            "PRC Registered Pharmacist",
            "Clinical Medication Review",
            "Prescription Dispensing & Compounding",
            "Pharmacovigilance & Drug Safety",
            "FDA / DOH Regulatory Compliance",
            "Hospital Pharmacy Inventory Management",
        ],
        "preferred_skills": [
            "Hospital Information Systems (HIS)",
            "Antimicrobial Stewardship",
            "Cold-Chain Logistics",
            "Clinical Pharmacy Certification",
        ],
        "rubric_weights": {
            "required_skills": 30,
            "experience": 25,
            "education": 25,
            "preferred_skills": 10,
            "projects": 10,
        },
        "status": JobStatus.PUBLISHED,
    },
    {
        "title": "Senior Medical Technologist (Laboratory Lead)",
        "department": "Healthcare & Medical",
        "employment_type": "Shifting",
        "location": "Cebu City / On-site",
        "minimum_experience": "3-5 years",
        "education_requirement": "BS in Medical Technology / Medical Laboratory Science with active PRC Registered Medical Technologist (RMT) License",
        "salary_min": 52000,
        "salary_max": 75000,
        "job_overview": (
            "Lead clinical diagnostic laboratory operations covering hematology, clinical chemistry, serology, and "
            "quality assurance verification. You will supervise bench technologists and uphold DOH laboratory "
            "accreditation standards."
        ),
        "description": (
            "• Perform and validate complex diagnostic tests across hematology, clinical chemistry, and microbiology.\n"
            "• Oversee daily analyzer calibration, internal quality control (IQC), and external quality assessment (NEQAS).\n"
            "• Supervise bench medical technologists, review critical lab values, and authorize release of results.\n"
            "• Enforce biosafety protocols, specimen integrity standards, and hazardous waste disposal procedures.\n"
            "• Manage laboratory reagent inventory, preventive equipment maintenance, and DOH audit readiness."
        ),
        "required_skills": [
            "PRC Registered Medical Technologist",
            "Clinical Chemistry & Hematology",
            "Laboratory Quality Control (IQC / NEQAS)",
            "Automated Diagnostic Analyzers",
            "Biosafety & Specimen Handling",
            "Laboratory Information Systems (LIS)",
        ],
        "preferred_skills": [
            "Drug Testing Analyst Certification",
            "HIV Proficiency Training",
            "ISO 15189 Accreditation Experience",
            "Molecular Diagnostics (PCR)",
        ],
        "rubric_weights": {
            "required_skills": 30,
            "experience": 25,
            "education": 25,
            "preferred_skills": 10,
            "projects": 10,
        },
        "status": JobStatus.PUBLISHED,
    },
    # =========================================================================
    # 6. BPO & Customer Support (3 roles)
    # =========================================================================
    {
        "title": "Omnichannel Customer Support Representative",
        "department": "BPO & Customer Support",
        "employment_type": "Shifting",
        "location": "Pasig (Ortigas) / Hybrid",
        "minimum_experience": "0-1 year",
        "education_requirement": "High School / Senior High School Diploma or completed at least 2 years of college",
        "salary_min": 24000,
        "salary_max": 34000,
        "job_overview": (
            "Deliver empathetic, accurate first-contact resolution to international customers across voice, live "
            "chat, and email channels. You will work in a hybrid Ortigas setup with structured training and clear "
            "performance metrics."
        ),
        "description": (
            "• Respond to inbound customer inquiries regarding orders, billing, and account access via voice, chat, and email.\n"
            "• Log detailed interaction summaries, case dispositions, and escalation tickets in Zendesk.\n"
            "• Follow knowledge-base procedures to resolve issues accurately while upholding data privacy standards.\n"
            "• Consistently meet service targets for Customer Satisfaction (CSAT), First Contact Resolution, and AHT.\n"
            "• Flag recurring customer friction points to team leads for knowledge-base improvements."
        ),
        "required_skills": [
            "Customer Service & Empathy",
            "Inbound Voice & Chat Support",
            "English Written & Verbal Fluency",
            "Ticketing Systems (Zendesk)",
            "Issue Troubleshooting",
        ],
        "preferred_skills": [
            "Salesforce Service Cloud",
            "E-Commerce or Billing Support",
            "Live Chat Multitasking",
            "De-Escalation Techniques",
        ],
        "rubric_weights": {
            "required_skills": 35,
            "experience": 30,
            "education": 5,
            "preferred_skills": 20,
            "projects": 10,
        },
        "status": JobStatus.PUBLISHED,
    },
    {
        "title": "Technical Support Specialist (Tier 2 SaaS)",
        "department": "BPO & Customer Support",
        "employment_type": "Contract",
        "location": "Manila / Remote",
        "minimum_experience": "1-2 years",
        "education_requirement": "College graduate or equivalent technical support experience in SaaS or IT helpdesk",
        "salary_min": 40000,
        "salary_max": 58000,
        "job_overview": (
            "Investigate and resolve escalated Tier 2 software, API integration, and account configuration issues "
            "for global B2B SaaS customers in a fully remote contract role. You will bridge customer support and "
            "product engineering."
        ),
        "description": (
            "• Diagnose escalated Tier 2 technical tickets involving web apps, SSO authentication, and API webhooks.\n"
            "• Reproduce customer-reported defects, inspect browser console/network logs, and document root causes.\n"
            "• File structured bug escalations in Jira with reproduction steps, payloads, and severity impact.\n"
            "• Author and update self-service troubleshooting guides and internal runbooks for Tier 1 agents.\n"
            "• Maintain SLA adherence and proactive status communication with enterprise client stakeholders."
        ),
        "required_skills": [
            "Tier 2 Technical Troubleshooting",
            "SaaS Application Support",
            "API & Webhook Diagnostics (Postman / DevTools)",
            "Zendesk & Jira Escalations",
            "Technical Documentation",
            "SLA Management",
        ],
        "preferred_skills": [
            "Basic SQL Querying",
            "SSO / OAuth / SAML Troubleshooting",
            "Intercom / Freshdesk",
            "ITIL Foundation",
        ],
        "rubric_weights": {
            "required_skills": 40,
            "experience": 25,
            "education": 10,
            "preferred_skills": 15,
            "projects": 10,
        },
        "status": JobStatus.PUBLISHED,
    },
    {
        "title": "BPO Operations & Quality Assurance Team Lead",
        "department": "BPO & Customer Support",
        "employment_type": "Full-time",
        "location": "Taguig (BGC) / Hybrid",
        "minimum_experience": "3-5 years",
        "education_requirement": "Bachelor's degree in any field or equivalent BPO leadership experience",
        "salary_min": 68000,
        "salary_max": 95000,
        "job_overview": (
            "Lead a team of 15–20 omnichannel customer support specialists, overseeing daily SLA delivery, quality "
            "calibration, and agent coaching. You will partner with client stakeholders to continuously improve "
            "CSAT and operational efficiency."
        ),
        "description": (
            "• Supervise daily shift operations, queue adherence, and staffing coverage to meet client SLAs.\n"
            "• Conduct weekly call/chat quality audits, calibration sessions, and 1-on-1 performance coaching.\n"
            "• Analyze CSAT, FCR, AHT, and attrition trends to implement targeted action plans.\n"
            "• Handle high-priority customer escalations and prepare weekly client business review decks.\n"
            "• Collaborate with workforce management and training teams on onboarding and upskilling."
        ),
        "required_skills": [
            "BPO Team Leadership & People Management",
            "Quality Assurance Auditing & Calibration",
            "KPI & SLA Analytics (CSAT / FCR / AHT)",
            "Escalation Management",
            "Workforce & Shift Scheduling",
            "Client Stakeholder Reporting",
        ],
        "preferred_skills": [
            "Six Sigma Yellow / Green Belt",
            "Zendesk Explore / Power BI",
            "COPC Standards",
            "Contact Center Coaching Frameworks",
        ],
        "rubric_weights": {
            "required_skills": 35,
            "experience": 30,
            "education": 10,
            "preferred_skills": 10,
            "projects": 15,
        },
        "status": JobStatus.PUBLISHED,
    },
]


class Command(BaseCommand):
    help = "Seed validated multi-industry job postings (3 per field across 6 industries)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete all existing Job and ArchivedJob records before seeding.",
        )
        parser.add_argument(
            "--company-id",
            type=int,
            default=None,
            help="Company ID to own the seeded jobs (defaults to the first existing Company).",
        )

    def _resolve_company(self, company_id):
        if company_id is not None:
            company = Company.objects.filter(pk=company_id).first()
            if not company:
                raise CommandError(f"Company with id={company_id} does not exist.")
            return company

        company = Company.objects.order_by("id").first()
        if company:
            return company

        return Company.objects.create(
            name="Aurevia",
            description="Multi-industry enterprise operating across IT, Finance, Engineering, Sales, Healthcare, and BPO.",
            website="https://aurevia.example.com",
        )

    def _validate_catalog(self, company):
        errors = []
        for idx, entry in enumerate(MULTI_INDUSTRY_JOBS, start=1):
            payload = {**entry, "company": company.id}
            serializer = JobSerializer(data=payload)
            if not serializer.is_valid():
                errors.append(f"Job #{idx} ({entry['title']}): serializer errors: {serializer.errors}")
                continue

            lint_result = lint_job_requisition(
                title=entry["title"],
                description=entry["description"],
                required_skills=entry["required_skills"],
                education=entry["education_requirement"],
                job_overview=entry["job_overview"],
            )
            if not lint_result["is_clean"]:
                errors.append(f"Job #{idx} ({entry['title']}): linter flags: {lint_result['flags']}")

        if errors:
            raise CommandError("Seed catalog validation failed:\n" + "\n".join(errors))

    def handle(self, *args, **options):
        company = self._resolve_company(options["company_id"])
        self._validate_catalog(company)

        with transaction.atomic():
            if options["reset"]:
                archived_deleted, _ = ArchivedJob.objects.all().delete()
                jobs_deleted, cascade_details = Job.objects.all().delete()
                self.stdout.write(
                    self.style.WARNING(
                        f"Reset complete: deleted {archived_deleted} ArchivedJob row(s) and "
                        f"{jobs_deleted} Job/cascade row(s) ({cascade_details})."
                    )
                )

            created_count = 0
            updated_count = 0
            for data in MULTI_INDUSTRY_JOBS:
                defaults = {**data, "company": company}
                _, created = Job.objects.update_or_create(
                    company=company,
                    title=data["title"],
                    defaults=defaults,
                )
                if created:
                    created_count += 1
                else:
                    updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(MULTI_INDUSTRY_JOBS)} jobs for company '{company.name}' (id={company.id}): "
                f"{created_count} created, {updated_count} updated."
            )
        )
