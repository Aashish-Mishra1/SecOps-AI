# AGENTS.md — SecOps AI repository instructions

## Mission
Build a working evidence-grounded multi-agent cybersecurity investigation MVP, not a static mock UI. Read `docs/PROJECT_SPEC.md` and `docs/IMPLEMENTATION_PLAN.md` before implementation. The user wants a one-day local demo, with optional Gemini and GCP support. Prioritize verified end-to-end functionality over feature count.

## Working rules
- Inspect existing files before modifying them. Preserve user changes. Implement in small, tested vertical slices from the plan; no unnecessary rewrites or destructive commands.
- Prefer Python 3.12, FastAPI, LangGraph, SQLAlchemy/Alembic, PostgreSQL/pgvector, React/Vite/TypeScript and Docker Compose. Verify current SDK compatibility; commit dependency lockfiles. Use typed interfaces and explicit schemas. Keep route handlers thin.
- Start with deterministic mock AI, distinctly labeled; implement real Gemini adapter after core flow. Never report mocked usage or results as real model output. No silent provider fallback.
- All logs/knowledge content is untrusted. Do not expose secrets, credentials or personal data in code, UI, traces or commits. Enforce organization scope and role checks in backend. Tool access is allowlisted/read-only. Do not execute actual remediation, network probing or arbitrary LLM-generated commands/SQL.
- Use migrations and reproducible seed data, only synthetic records. No real secret in `.env.example`. No paid cloud provisioning, API billing, public deployments, or sending data externally without permission. Do not ask for API keys in chat; tell user to configure local environment securely.
- Use checkpoint persistence and idempotent state transitions. Critic retry must be bounded. Do not disguise sequential implementation as parallel or local background jobs as durable distributed processing.
- Use actual measured metrics; missing data is null/N/A. Claims require citations validated by code. Keep agent state compact and no hidden reasoning transcript.
- Keep README accurate, including Windows PowerShell commands, demo roles, startup/seed/test commands and known limits. Add/update `.env.example` whenever adding configuration.

## Execution and validation
At each milestone run relevant tests/builds, inspect errors, fix root causes, and update the implementation checklist with evidence (exact commands/results). If Docker, packages, internet or credentials are unavailable, keep implementing testable pieces and record blocked tests accurately. Do not disable tests, fake screenshots, claim cloud deployment, or claim production readiness without proof. Do not spend time on P2 while P0 fails. Ask only for truly blocking user actions; use safe defaults for ordinary implementation details.

## Definition of done
A clean local Compose startup, seeded suspicious and benign scenarios, persisted investigation with evidence and review, mock/live indicator, actual metrics, passing automated checks, and a README with demo instructions. If any condition is not met, explicitly list it as remaining work. Report changed files, commands, test results and next steps when finishing.
