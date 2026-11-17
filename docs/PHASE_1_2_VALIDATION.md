# Phase 1–2 implementation and validation

Scope: the user's Phase 1 foundation and Phase 2 database/authentication/ingestion/RAG request only. Repository M0 and M1 are complete. Registration, standalone cited RAG and Gemini adapter contracts are also implemented as requested. M2 orchestration, M3 dashboards, advanced M4 tracing/evaluation and M5 deployment remain deferred. This document does not claim the full product definition of done.

## Environment audit

Observed during this session (client date: 17 November 2026):

| Component | Observed result |
|---|---|
| Workspace | Initially only AGENTS.md and the two design documents; all read before implementation |
| Git | 2.50.0.windows.2; initialized repository because `.git` was absent |
| Python host | 3.13.14; also installed Python 3.12.16 via uv |
| Python container | 3.12.12, used for integration tests |
| uv | 0.13.9; lockfile resolved and frozen sync verified in Docker |
| Node / npm | 22.14.0 / 10.9.2 |
| Docker / Compose | 29.9.3 / v5.7.0 |
| WSL | 2.7.3.0, kernel 6.6.114.1, default WSL version 2 |
| Docker engine | Initially stopped; started Docker Desktop and verified Linux engine |
| Database | pgvector/pgvector:0.8.2-pg16, healthy |

Current primary documentation was consulted for Google Gen AI SDK, pgvector and Vite compatibility: [Gen AI SDK](https://googleapis.github.io/python-genai/), [pgvector](https://github.com/pgvector/pgvector), [Vite](https://vite.dev/guide/). Installed SDK types were inspected and exercised by tests. No Google API call or cloud deployment was made.

## Executed checks

Commands below ran from the repository root unless a directory is stated. Local backend commands first loaded `. .\scripts\dev-env.ps1`.

| Check | Command / result |
|---|---|
| Configuration gate | `cd backend; .\.venv\Scripts\python -m pytest tests/test_foundation.py -q` — 1 passed |
| Schema | `alembic upgrade head`; `alembic check` — migration succeeds; no new upgrade operations detected |
| Host suite | `cd backend; .\.venv\Scripts\python -m pytest -q` — 28 passed, one upstream Starlette/httpx deprecation warning, 16.63 s |
| Python 3.12 suite | `docker compose run --rm backend uv run --frozen pytest -q` — 28 passed, same warning, 31.72 s |
| Final SDK correction | `cd backend; .\.venv\Scripts\python -m pytest tests/test_provider.py -q` — 3 passed; verifies available embedding token counts and rejection of provider-truncated input |
| Final complete suite | `docker compose -p secops-verify run --rm backend uv run --frozen pytest -q` — 28 passed, one upstream deprecation warning, 19.17 s, including the SDK correction |
| Python lint | `.\backend\.venv\Scripts\ruff check backend scripts/smoke.py` — all checks passed |
| Frontend | `cd frontend; npm install; npm run build` — TypeScript check and Vite build pass |
| Container builds | `docker compose build` — backend frozen uv sync and frontend `npm ci`/build pass |
| Production dependency audit | `cd frontend; npm audit --omit=dev` — zero vulnerabilities |
| Full dependency audit | `cd frontend; npm audit --json` — four high-severity development dependency findings, unresolved and documented |
| Fresh Compose startup | `docker compose -p secops-verify up --build -d --wait` — fresh named database volume, automatic migration, all three containers healthy |
| Seed idempotency | `docker compose -p secops-verify exec -T backend python -m app.seed` twice — 155 events remain; no duplicate users/alerts/documents |
| End-to-end HTTP | `. .\scripts\dev-env.ps1; python scripts/smoke.py` through port 5173 — passed: login, 151 suspicious-case events, alerts, cited mock RAG; one cited source, measured retrieval 17.85 ms in the fresh-volume run (single observation, not a benchmark) |
| Public endpoints | GET `http://localhost:8000/api/v1/health/ready` and GET `http://localhost:5173` — ready/mock and HTTP 200 |
| Secrets exclusion | `git check-ignore .env backend/.venv frontend/node_modules` — all excluded |
| Generated-secret scan | Compared generated local secrets against all 66 candidate repository files — no matches |

Every integration suite creates its own random test database, applies/downgrades/reapplies the migration, checks schema drift, tests against real PostgreSQL full text and pgvector, and drops only its test database. It does not mutate the seeded demo database.

## Failure handling and fixes during validation

- Dependency downloads were initially slow; no startup success was claimed until health checks passed.
- First Windows npm installation failed launching esbuild; a retry succeeded. A later `npm ci` encountered EPERM on locked native modules; `npm install` restored the local environment and the TypeScript/Vite build passed. Container `npm ci` succeeded. If this recurs locally, close tools holding native modules or use the container build; do not disable OS protection.
- The test database's initial Windows `localhost` connection stalled on IPv6. The dev environment now uses `127.0.0.1`, and the test admin connection has a five-second timeout. Subsequent suites passed.
- A one-line PowerShell Python inspection lost quotes around an OpenAPI dictionary key; the OpenAPI HTTP integration test verified the schema instead. This was a command quoting issue, not an API failure.
- Alembic's path-separator warning was fixed with `path_separator=os`. The remaining Starlette/httpx upstream deprecation is reported rather than suppressed.
- Inspection of google-genai 1.75.0 showed embedding usage under per-embedding `statistics.token_count`; the adapter now records it when all counts are available, returns null otherwise, and rejects truncated inputs.

## Coverage and boundaries

Tests exercise health/OpenAPI, configuration rejection, registration and non-escalation, correct/incorrect passwords, expired tokens, reviewer write denial, scoped alerts/events/documents, atomic JSON/CSV imports, duplicates, pagination limits, timestamp/IP normalization, all retrieval modes, metadata filters, SHA-256 deduplication, page citations in a generated text PDF, encrypted/malformed/oversized inputs, provider failure and idempotent retry, invalid citations, empty knowledge, model-filtered reindex, advisory-lock conflicts, rate limiting, RRF, deterministic embeddings, chunk offsets/overlap, untrusted source text and SDK schemas/usage. Gemini tests replace the client with test doubles; their measurements are test fixtures, not real model usage.

## Remaining work

- Authorized live Gemini/Vertex smoke test and retrieval-quality assessment.
- Upstream resolution of the four frontend development dependency audit findings; production-only audit is clean.
- Remote CI execution, production hardening, hostile-file sandboxing, database RLS, SSO/revocation and distributed limits.
- LangGraph orchestration/checkpoints, specialists, critic/reflection, human review, investigation API/UI, historical observability and cloud deployment, all explicitly outside this request.

Detailed project tree, database table summary, API inventory, configuration and next-phase integration instructions are in README.md.
