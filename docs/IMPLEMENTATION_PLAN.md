# SecOps AI — One-day implementation plan for Codex

Timeboxes are planning targets, not promises. If time is limited, stop expanding scope and make the end-to-end path reliable. Mark each item only after actual verification.

## Current execution scope: user-requested Phases 1–2 only

M0/M1 are verified below. Additional Phase 2 work includes analyst-only local registration, standalone cited RAG, Gemini adapter contracts and real PostgreSQL integration tests. M2 and later workflows are not authorized in this iteration. Exact commands, results, environment issues and remaining work are recorded in [PHASE_1_2_VALIDATION.md](PHASE_1_2_VALIDATION.md). A live Google call, production readiness or full investigation demo is not claimed.

- [x] 28 host unit/integration tests pass against real PostgreSQL/pgvector.
- [x] 28 tests pass on container Python 3.12; migration rollback/reapply and schema drift are checked.
- [x] Frontend TypeScript/Vite build and container `npm ci` build pass.
- [x] Fresh Compose volume initializes with all services healthy; repeated seed and HTTP retrieval smoke pass.
- [x] Mock mode, null unavailable metrics, citation/tenant validation and explicit external AI opt-in verified.
- [ ] Live Gemini/Vertex smoke test (no external-call permission or configured credentials used).
- [ ] Resolve four frontend development dependency audit findings upstream; production-only audit is clean.

## M0 — Scaffold (45 minutes)
- [x] Inspect repository; create structure and dependency files; add env validation, .gitignore, Compose and Dockerfiles.
- [x] PostgreSQL + pgvector readiness, Alembic base schema, health endpoints, frontend shell.
- [x] README startup commands and test skeleton.
Gate: containers start or exact blocker recorded; `/api/v1/health/ready` succeeds with database.

## M1 — Data and access (90 minutes)
- [x] Implement password hashing, login, role and organization checks, seeded analyst/reviewer.
- [x] Implement alerts, event import and idempotent seed; suspicious and benign cases.
- [x] Implement upload, chunking, mock embeddings, pgvector + lexical RRF, evidence metadata.
Gate: auth/import/retrieval integration tests, duplicate import and invalid upload tests.

## M2 — Agent workflow (120 minutes)
- [x] Define typed model provider, mock adapter and schemas; implement graph nodes and bounded routing.
- [x] Implement actual read-only tools and evidence validation; persist agent runs and status.
- [x] Add critic targeted retry; checkpoint saver, review interrupt/resume, role checks, idempotency.
- [x] Add investigation HTTP endpoints, failure handling and tests.
Gate: suspicious case goes from alert to cited draft to reviewed report, benign case remains proportionate; restart review test.

## M3 — Frontend vertical slice (90 minutes)
- [x] Login, app shell, alerts, import, knowledge base.
- [x] Investigation timeline with polling, evidence drawer, review and export.
- [x] Actual metrics dashboard, missing-data states and mock badge.
Gate: frontend typecheck/build and manual or scripted end-to-end smoke test.

## M4 — Reliability, metrics and documentation (60 minutes)
- [x] OpenTelemetry spans, performance metrics and evaluation fixture/script.
- [x] Provider failure, auth bypass, injection and retry-cap tests; CI workflow.
- [x] Run all tests, README and demo script; document failed/blocked checks honestly.
Gate: P0 acceptance matrix reviewed; reproducible local demo.

## M5 — Live AI and GCP (optional, 60–90 minutes after P0)
- [ ] Google Gen AI adapter with configurable models/dimensions, schema validation and actual usage metadata.
- [ ] Re-index knowledge base for real embeddings; opt-in smoke test with user-provided environment credentials.
- [ ] Add GCP deployment docs/config; request explicit approval before billable provisioning or deployment.
Gate: record actual live run if credentials exist; otherwise say not tested. Cloud launch only confirmed after successful cloud smoke test.

## Seed fixtures
Use deterministic IDs and reserved documentation IPs (e.g. 192.0.2.45, 198.51.100.8) and fictional account identifiers. Suspicious example: 150 failed attempts in a 10-minute period, followed by success; an offline indicator labeled suspicious in seed, a policy on repeated login failures, MFA and session review. Benign example: a service account with known maintenance window and expected login retry pattern; no unsupported compromise claim. Add explicit missing-policy and unknown-indicator cases. No external IP contact. Make run labels and timestamps reproducible. Use seed data for tests only.

## Suggested first Codex prompt
Read AGENTS.md, docs/PROJECT_SPEC.md and docs/IMPLEMENTATION_PLAN.md. Inspect the current workspace, then implement this project from M0 onward. Work in milestone-sized changes, run checks and fix problems before proceeding. Use deterministic mock mode first so the app runs without credentials. Implement working backend and frontend, not placeholders. Continue through P0 independently using safe defaults; stop for required user permission or inaccessible dependencies only. Do not create paid cloud resources, send data to external services, or fabricate test/metric results. At the end produce a concise completion report with setup commands for Windows PowerShell, test results, known limitations and unchecked tasks. Start by inspecting the repository and implementing M0.

## Follow-up prompts (use separately if the agent stops or context resets)
1. Read the repository instructions and current checklist. Verify M0 and implement M1, running relevant database/API tests.
2. Implement M2 end to end with persisted LangGraph checkpoints, a bounded critique loop, citation validation, approval interrupt and integration tests. Do not skip actual graph invocation.
3. Implement M3 against the actual REST API; verify loading/error states and run the frontend build. Run the suspicious-case demo.
4. Complete M4, run all tests, fix failures and update the README and checklist. Report actual results.
5. Implement M5 Gemini adapter and deployment configuration. Do not provision services or make live API calls unless I have authorized them and supplied credentials through the environment. Distinguish deployment plan from deployed service.

## Final self-review prompt
Act as a senior backend/AI engineer reviewing this repository against the spec. Check authorization, tool boundaries, citation validity, retries, state persistence, duplicate jobs, secret handling, real-vs-mock metrics, failure states, and README commands. Fix concrete defects, run tests/build, and report remaining risks without claiming production readiness. Do not expand scope unnecessarily.
