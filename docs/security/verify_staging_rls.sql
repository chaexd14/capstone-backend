-- =============================================================================
-- TalentMatch Staging & Production Read-Only Security Verification Script
-- File: docs/security/verify_staging_rls.sql
--
-- Purpose:
--   Strictly read-only diagnostic queries to verify Row-Level Security (RLS),
--   policy inventory, table/column/sequence permissions, helper function
--   hardening, and pre-migration application uniqueness on Supabase PostgreSQL.
--
-- Usage:
--   Run against staging (and later production) BEFORE and AFTER applying
--   Django migrations. This script performs zero state modifications.
--
-- Important Note on Effective SQL Privileges vs. RLS Row Filtering:
--   Effective SQL privilege checks (has_table_privilege, has_column_privilege,
--   has_sequence_privilege) confirm that Supabase client roles (anon,
--   authenticated) cannot execute direct INSERT, UPDATE, or DELETE statements.
--   However, SQL privilege checks alone do NOT prove runtime RLS row-filtering
--   behavior on SELECT. Always review Sections 1, 3, and 3b for RLS enablement
--   and policy definitions, and run post-migration role-based runtime tests in
--   staging after migration approval to confirm row-level visibility boundaries.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Protected Table Existence and RLS Enforcement Status
--    Expected: All 7 protected tables exist with rls_enabled = true.
-- -----------------------------------------------------------------------------
WITH expected_tables(table_name) AS (
    VALUES
        ('accounts_userrole'),
        ('resumes_resume'),
        ('resumes_matchresult'),
        ('jobs_job'),
        ('jobs_archivedjob'),
        ('applications_application'),
        ('notifications_notification')
)
SELECT
    e.table_name AS expected_table,
    (c.oid IS NOT NULL) AS exists_in_public,
    COALESCE(c.relrowsecurity, false) AS rls_enabled,
    COALESCE(c.relforcerowsecurity, false) AS force_rls_enabled
FROM expected_tables e
LEFT JOIN pg_catalog.pg_namespace n
    ON n.nspname = 'public'
LEFT JOIN pg_catalog.pg_class c
    ON c.relnamespace = n.oid
   AND c.relname = e.table_name
   AND c.relkind = 'r'
ORDER BY e.table_name;

-- -----------------------------------------------------------------------------
-- 2. Full Public Schema Table Inventory and RLS Status
--    Identifies any unexpected tables in public schema and their RLS flags.
-- -----------------------------------------------------------------------------
SELECT
    n.nspname AS schema_name,
    c.relname AS table_name,
    c.relrowsecurity AS rls_enabled,
    c.relforcerowsecurity AS force_rls_enabled,
    pg_catalog.pg_get_userbyid(c.relowner) AS table_owner
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n
    ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind = 'r'
ORDER BY c.relname;

-- -----------------------------------------------------------------------------
-- 3. Complete RLS Policy Inventory on Protected Tables
--    Expected: Only the authoritative policies defined by migrations exist;
--    zero policies reference user_metadata or raw_user_meta_data.
-- -----------------------------------------------------------------------------
SELECT
    schemaname,
    tablename,
    policyname,
    permissive,
    roles,
    cmd,
    qual,
    with_check,
    (
        COALESCE(qual, '') ILIKE '%user_metadata%'
        OR COALESCE(with_check, '') ILIKE '%user_metadata%'
        OR COALESCE(qual, '') ILIKE '%raw_user_meta_data%'
        OR COALESCE(with_check, '') ILIKE '%raw_user_meta_data%'
    ) AS has_insecure_metadata_reference
FROM pg_catalog.pg_policies
WHERE schemaname = 'public'
  AND tablename IN (
      'accounts_userrole',
      'resumes_resume',
      'resumes_matchresult',
      'jobs_job',
      'jobs_archivedjob',
      'applications_application',
      'notifications_notification'
  )
ORDER BY tablename, policyname;

-- -----------------------------------------------------------------------------
-- 3b. Authoritative Policy Presence & Forbidden Client Write Policy Absence
--     Expected:
--       - public_read_published_jobs_present     = true
--       - applicant_select_applications_present  = true
--       - applicant_select_resumes_present       = true
--       - applicant_select_notifications_present = true
--       - applicant_select_userrole_present      = true
--       - recruiter_applications_policy_present  = true
--       - recruiter_resumes_policy_present       = true
--       - recruiter_matchresult_policy_present   = true
--       - recruiter_jobs_policy_present          = true
--       - recruiter_archivedjobs_policy_present  = true
--       - recruiter_notifications_policy_present = true
--       - applicant_insert_applications_present  = false
--       - applicant_insert_resumes_present       = false
--       - applicant_matchresult_policy_present   = false
-- -----------------------------------------------------------------------------
SELECT
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'jobs_job'
          AND policyname = 'Allow public read of published jobs'
          AND cmd = 'SELECT'
    ) AS public_read_published_jobs_present,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'applications_application'
          AND policyname = 'Allow applicants to view their own applications'
          AND cmd = 'SELECT'
    ) AS applicant_select_applications_present,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'resumes_resume'
          AND policyname = 'Allow applicants to view their own resume'
          AND cmd = 'SELECT'
    ) AS applicant_select_resumes_present,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'notifications_notification'
          AND policyname = 'Allow applicants to view their own notifications'
          AND cmd = 'SELECT'
    ) AS applicant_select_notifications_present,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'accounts_userrole'
          AND policyname = 'Allow users to view own role profile'
          AND cmd = 'SELECT'
    ) AS applicant_select_userrole_present,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'applications_application'
          AND policyname = 'Allow recruiters full access to applications'
    ) AS recruiter_applications_policy_present,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'resumes_resume'
          AND policyname = 'Allow recruiters full access to resumes'
    ) AS recruiter_resumes_policy_present,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'resumes_matchresult'
          AND policyname = 'Allow recruiters full access to match results'
    ) AS recruiter_matchresult_policy_present,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'jobs_job'
          AND policyname = 'Allow recruiters to manage jobs'
    ) AS recruiter_jobs_policy_present,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'jobs_archivedjob'
          AND policyname = 'Allow recruiters to manage archived jobs'
    ) AS recruiter_archivedjobs_policy_present,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'notifications_notification'
          AND policyname = 'Allow recruiters full access to notifications'
    ) AS recruiter_notifications_policy_present,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'applications_application'
          AND (
              policyname = 'Allow applicants to insert their own applications'
              OR cmd = 'INSERT'
          )
    ) AS applicant_insert_applications_present,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'resumes_resume'
          AND (
              policyname = 'Allow applicants to insert their own resume'
              OR cmd = 'INSERT'
          )
    ) AS applicant_insert_resumes_present,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'resumes_matchresult'
          AND policyname ILIKE '%applicant%'
    ) AS applicant_matchresult_policy_present;

-- -----------------------------------------------------------------------------
-- 4. Table-Level Privilege Matrix for Supabase & Backend Roles
--    Verifies least-privilege table access for anon, authenticated,
--    service_role, and the active backend connection user.
-- -----------------------------------------------------------------------------
SELECT
    grantee,
    table_schema,
    table_name,
    privilege_type,
    is_grantable
FROM information_schema.role_table_grants
WHERE table_schema = 'public'
   AND table_name IN (
       'accounts_userrole',
       'resumes_resume',
       'resumes_matchresult',
       'jobs_job',
       'jobs_archivedjob',
       'applications_application',
       'notifications_notification'
   )
   AND (
       grantee IN ('anon', 'authenticated', 'service_role', 'postgres')
       OR grantee = current_user
   )
ORDER BY table_name, grantee, privilege_type;

-- -----------------------------------------------------------------------------
-- 4b. Effective Write Privilege Check Across All 7 Protected Tables
--     Expected: ALL values are false (authenticated has SELECT only across all
--     seven protected tables and zero column-level UPDATE privileges;
--     Django's postgres role is the exclusive trusted write path).
-- -----------------------------------------------------------------------------
SELECT
    has_table_privilege(
        'authenticated',
        'public.applications_application',
        'INSERT'
    ) AS authenticated_can_insert_applications,
    has_table_privilege(
        'authenticated',
        'public.applications_application',
        'UPDATE'
    ) AS authenticated_can_update_applications,
    has_table_privilege(
        'authenticated',
        'public.applications_application',
        'DELETE'
    ) AS authenticated_can_delete_applications,
    has_table_privilege(
        'authenticated',
        'public.resumes_resume',
        'INSERT'
    ) AS authenticated_can_insert_resumes,
    has_table_privilege(
        'authenticated',
        'public.resumes_resume',
        'UPDATE'
    ) AS authenticated_can_update_resumes,
    has_table_privilege(
        'authenticated',
        'public.resumes_resume',
        'DELETE'
    ) AS authenticated_can_delete_resumes,
    has_table_privilege(
        'authenticated',
        'public.jobs_job',
        'INSERT'
    ) AS authenticated_can_insert_jobs,
    has_table_privilege(
        'authenticated',
        'public.jobs_job',
        'UPDATE'
    ) AS authenticated_can_update_jobs,
    has_table_privilege(
        'authenticated',
        'public.jobs_job',
        'DELETE'
    ) AS authenticated_can_delete_jobs,
    has_table_privilege(
        'authenticated',
        'public.jobs_archivedjob',
        'INSERT'
    ) AS authenticated_can_insert_archivedjobs,
    has_table_privilege(
        'authenticated',
        'public.jobs_archivedjob',
        'UPDATE'
    ) AS authenticated_can_update_archivedjobs,
    has_table_privilege(
        'authenticated',
        'public.jobs_archivedjob',
        'DELETE'
    ) AS authenticated_can_delete_archivedjobs,
    has_table_privilege(
        'authenticated',
        'public.notifications_notification',
        'INSERT'
    ) AS authenticated_can_insert_notifications,
    has_table_privilege(
        'authenticated',
        'public.notifications_notification',
        'UPDATE'
    ) AS authenticated_can_update_notifications,
    has_table_privilege(
        'authenticated',
        'public.notifications_notification',
        'DELETE'
    ) AS authenticated_can_delete_notifications,
    has_column_privilege(
        'authenticated',
        'public.notifications_notification',
        'is_read',
        'UPDATE'
    ) AS authenticated_can_update_notification_is_read,
    has_table_privilege(
        'authenticated',
        'public.accounts_userrole',
        'INSERT'
    ) AS authenticated_can_insert_userrole,
    has_table_privilege(
        'authenticated',
        'public.accounts_userrole',
        'UPDATE'
    ) AS authenticated_can_update_userrole,
    has_table_privilege(
        'authenticated',
        'public.accounts_userrole',
        'DELETE'
    ) AS authenticated_can_delete_userrole,
    has_table_privilege(
        'authenticated',
        'public.resumes_matchresult',
        'INSERT'
    ) AS authenticated_can_insert_matchresult,
    has_table_privilege(
        'authenticated',
        'public.resumes_matchresult',
        'UPDATE'
    ) AS authenticated_can_update_matchresult,
    has_table_privilege(
        'authenticated',
        'public.resumes_matchresult',
        'DELETE'
    ) AS authenticated_can_delete_matchresult;

-- -----------------------------------------------------------------------------
-- 4c. Effective Table Privilege Matrix for anon, authenticated & Django postgres
--     Expected across all 7 protected tables:
--       - anon_can_select = true ONLY on jobs_job (false on all other 6 tables)
--       - anon_can_insert / anon_can_update / anon_can_delete = false on all 7
--       - authenticated_can_select = true on all 7 tables (row visibility
--         governed by RLS policies in Section 3)
--       - authenticated_can_insert / update / delete = false on all 7 tables
--       - postgres_can_select / insert / update / delete = true on all 7 tables
--       - postgres_bypassrls = true
-- -----------------------------------------------------------------------------
WITH expected_tables(table_name) AS (
    VALUES
        ('accounts_userrole'),
        ('resumes_resume'),
        ('resumes_matchresult'),
        ('jobs_job'),
        ('jobs_archivedjob'),
        ('applications_application'),
        ('notifications_notification')
)
SELECT
    e.table_name,
    pg_catalog.has_table_privilege('anon', 'public.' || e.table_name, 'SELECT') AS anon_can_select,
    pg_catalog.has_table_privilege('anon', 'public.' || e.table_name, 'INSERT') AS anon_can_insert,
    pg_catalog.has_table_privilege('anon', 'public.' || e.table_name, 'UPDATE') AS anon_can_update,
    pg_catalog.has_table_privilege('anon', 'public.' || e.table_name, 'DELETE') AS anon_can_delete,
    pg_catalog.has_table_privilege('authenticated', 'public.' || e.table_name, 'SELECT') AS authenticated_can_select,
    pg_catalog.has_table_privilege('authenticated', 'public.' || e.table_name, 'INSERT') AS authenticated_can_insert,
    pg_catalog.has_table_privilege('authenticated', 'public.' || e.table_name, 'UPDATE') AS authenticated_can_update,
    pg_catalog.has_table_privilege('authenticated', 'public.' || e.table_name, 'DELETE') AS authenticated_can_delete,
    pg_catalog.has_table_privilege('postgres', 'public.' || e.table_name, 'SELECT') AS postgres_can_select,
    pg_catalog.has_table_privilege('postgres', 'public.' || e.table_name, 'INSERT') AS postgres_can_insert,
    pg_catalog.has_table_privilege('postgres', 'public.' || e.table_name, 'UPDATE') AS postgres_can_update,
    pg_catalog.has_table_privilege('postgres', 'public.' || e.table_name, 'DELETE') AS postgres_can_delete,
    (SELECT r.rolbypassrls FROM pg_catalog.pg_roles r WHERE r.rolname = 'postgres') AS postgres_bypassrls
FROM expected_tables e
ORDER BY e.table_name;

-- -----------------------------------------------------------------------------
-- 5. Column-Level Privilege Audit Across All 7 Protected Tables
--    Expected: 0 rows with INSERT, UPDATE, or REFERENCES for anon or
--    authenticated (including on notifications_notification.is_read).
--    Only SELECT column privileges inherited from table-level SELECT may appear.
-- -----------------------------------------------------------------------------
SELECT
    grantee,
    table_schema,
    table_name,
    column_name,
    privilege_type,
    is_grantable
FROM information_schema.column_privileges
WHERE table_schema = 'public'
  AND table_name IN (
      'accounts_userrole',
      'resumes_resume',
      'resumes_matchresult',
      'jobs_job',
      'jobs_archivedjob',
      'applications_application',
      'notifications_notification'
  )
  AND grantee IN ('anon', 'authenticated')
ORDER BY table_name, grantee, column_name, privilege_type;

-- -----------------------------------------------------------------------------
-- 5b. Effective Column-Level Write Privilege Audit (has_column_privilege)
--     Detects any remaining column-specific INSERT or UPDATE capability for
--     anon or authenticated across every column of the 7 protected tables.
--     Expected: 0 rows returned.
-- -----------------------------------------------------------------------------
SELECT
    c.table_schema,
    c.table_name,
    c.column_name,
    pg_catalog.has_column_privilege('anon', 'public.' || c.table_name, c.column_name, 'INSERT') AS anon_col_insert,
    pg_catalog.has_column_privilege('anon', 'public.' || c.table_name, c.column_name, 'UPDATE') AS anon_col_update,
    pg_catalog.has_column_privilege('authenticated', 'public.' || c.table_name, c.column_name, 'INSERT') AS authenticated_col_insert,
    pg_catalog.has_column_privilege('authenticated', 'public.' || c.table_name, c.column_name, 'UPDATE') AS authenticated_col_update
FROM information_schema.columns c
WHERE c.table_schema = 'public'
  AND c.table_name IN (
      'accounts_userrole',
      'resumes_resume',
      'resumes_matchresult',
      'jobs_job',
      'jobs_archivedjob',
      'applications_application',
      'notifications_notification'
  )
  AND (
      pg_catalog.has_column_privilege('anon', 'public.' || c.table_name, c.column_name, 'INSERT')
      OR pg_catalog.has_column_privilege('anon', 'public.' || c.table_name, c.column_name, 'UPDATE')
      OR pg_catalog.has_column_privilege('authenticated', 'public.' || c.table_name, c.column_name, 'INSERT')
      OR pg_catalog.has_column_privilege('authenticated', 'public.' || c.table_name, c.column_name, 'UPDATE')
  )
ORDER BY c.table_name, c.column_name;

-- -----------------------------------------------------------------------------
-- 6. Sequence Privilege Audit in Public Schema
--    Expected for the 7 protected TalentMatch sequences
--    (accounts_userrole_id_seq, applications_application_id_seq,
--     jobs_job_id_seq, jobs_archivedjob_id_seq,
--     notifications_notification_id_seq, resumes_resume_id_seq,
--     resumes_matchresult_id_seq):
--      - anon_usage = false, anon_select = false, anon_update = false
--      - authenticated_usage = false, authenticated_select = false, authenticated_update = false
--      - postgres_usage = true, postgres_select = true, postgres_update = true
--      - backend_user_usage = true
-- -----------------------------------------------------------------------------
SELECT
    n.nspname AS sequence_schema,
    c.relname AS sequence_name,
    pg_catalog.has_sequence_privilege('anon', c.oid, 'USAGE') AS anon_usage,
    pg_catalog.has_sequence_privilege('anon', c.oid, 'SELECT') AS anon_select,
    pg_catalog.has_sequence_privilege('anon', c.oid, 'UPDATE') AS anon_update,
    pg_catalog.has_sequence_privilege('authenticated', c.oid, 'USAGE') AS authenticated_usage,
    pg_catalog.has_sequence_privilege('authenticated', c.oid, 'SELECT') AS authenticated_select,
    pg_catalog.has_sequence_privilege('authenticated', c.oid, 'UPDATE') AS authenticated_update,
    pg_catalog.has_sequence_privilege('postgres', c.oid, 'USAGE') AS postgres_usage,
    pg_catalog.has_sequence_privilege('postgres', c.oid, 'SELECT') AS postgres_select,
    pg_catalog.has_sequence_privilege('postgres', c.oid, 'UPDATE') AS postgres_update,
    pg_catalog.has_sequence_privilege(current_user, c.oid, 'USAGE') AS backend_user_usage
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n
    ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind = 'S'
ORDER BY c.relname;

-- -----------------------------------------------------------------------------
-- 7. Helper Function Security Metadata (public.is_recruiter_or_superadmin)
--    Expected: prosecdef = true (SECURITY DEFINER), proconfig includes
--    search_path=public, and anon cannot execute the function.
-- -----------------------------------------------------------------------------
SELECT
    n.nspname AS function_schema,
    p.proname AS function_name,
    pg_catalog.pg_get_userbyid(p.proowner) AS function_owner,
    p.prosecdef AS is_security_definer,
    p.provolatile AS volatility,
    p.proconfig AS function_config,
    p.proacl AS access_control_list,
    pg_catalog.has_function_privilege('anon', p.oid, 'EXECUTE') AS anon_can_execute,
    pg_catalog.has_function_privilege('authenticated', p.oid, 'EXECUTE') AS authenticated_can_execute
FROM pg_catalog.pg_proc p
JOIN pg_catalog.pg_namespace n
    ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.proname = 'is_recruiter_or_superadmin';

-- -----------------------------------------------------------------------------
-- 8. Pre-Migration Duplicate Detection on public.applications_application
--    Checks for existing duplicate (job_id, user_id) or (job_id, lower(email))
--    rows that would block unique constraint/index creation in migration 0007.
--    Expected: 0 rows returned by both queries.
-- -----------------------------------------------------------------------------
SELECT
    'duplicate_job_user_id' AS check_type,
    job_id,
    user_id::text AS identifier,
    COUNT(*) AS duplicate_count
FROM public.applications_application
WHERE user_id IS NOT NULL
GROUP BY job_id, user_id
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC, job_id;

SELECT
    'duplicate_job_email' AS check_type,
    job_id,
    LOWER(email) AS identifier,
    COUNT(*) AS duplicate_count
FROM public.applications_application
GROUP BY job_id, LOWER(email)
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC, job_id;
