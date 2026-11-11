# Recruiter Workspace Implementation Roadmap

This roadmap expands TalentMatch from candidate review into a complete, human-controlled recruitment workspace. Each phase has an independently testable release boundary.

| Phase | Scope | Release gate | Status |
|---|---|---|---|
| 1. Workflow foundation | Pipeline board, private notes, activity history, tasks/reminders, bulk stage moves, internal communication templates | Authorization tests, audit records, keyboard-accessible stage controls, responsive board | Implemented; automated verification passed |
| 2. Evaluation collaboration | Candidate comparison, structured interview scorecards, candidate assignment, mentions and feedback locking | Independent reviewer input, explainable comparisons, company scoping | Planned |
| 3. Talent organization | Tags, talent pools, saved searches/views, duplicate-candidate detection, job templates | Consent/retention rules, normalized duplicate review, reusable template controls | Planned |
| 4. Scheduling and communication | Interview availability, self-service booking, outbound email adapter, optional calendar adapters | Time-zone tests, delivery audit, retry/idempotency, integration fallback | Planned |
| 5. Decisions and offers | Offer approval workflow, revision history, approval permissions, acceptance tracking | Separation of duties, immutable approval history, compensation privacy | Planned |
| 6. Recruitment intelligence | Funnel analytics, stage aging, time-to-review/hire, source and workload reporting | Metric definitions, privacy thresholds, query-performance tests | Planned |

## Product guardrails

- Automated analysis supports evidence gathering and coordination; it does not hire, reject, or approve offers.
- Every consequential state change records the actor, time, previous state, and resulting state.
- Recruiter data remains company-scoped and applicant-safe serializers never expose internal workflow records.
- Internal workflow remains functional when external email or calendar integrations are unavailable.
- Accessibility, responsive behavior, auditability, and production tests are release requirements rather than follow-up polish.
