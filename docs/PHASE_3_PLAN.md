# Phase 3 implementation plan

Scope: backend orchestration only. Preserve Phase 1–2 APIs and frontend. Baseline: 28 tests passed on 18 November 2026 (17.68 seconds); clean Git worktree.

1. [x] Add LangGraph/checkpoint dependencies, additive migration, typed contracts, versioned prompts, bounded provider calls and tool infrastructure.
2. [x] Implement supervisor and concurrent log/knowledge/threat specialists, reducer state, targeted bounded reflection, remediation, reporting and persistent review interrupt.
3. [x] Add asynchronous local executor, idempotent creation/start/review/resume, startup reconciliation, scoped APIs and actual execution metrics.
4. [x] Test individual tools, provider failures, graph concurrency, tenant/role boundaries, checkpoint recovery and approval; preserve all baseline tests.
5. [x] Seed five investigation scenarios, run evaluation/smoke, update README/API/workflow documentation and record exact validation evidence.

Design decisions:
- PostgreSQL-backed checkpoints via the installed LangGraph saver, initialized separately from application Alembic tables.
- Parallel branches use isolated sessions and map/ID reducers, then join before remediation. A reflection round dispatches only requested specialists.
- Code validates factual claims against persisted scoped evidence. Model-selected tools are typed and allowlisted; query scope derives from the authorized alert, never model-selected organizations or arbitrary SQL.
- Local bounded thread-pool execution is not a durable queue. Database advisory locks prevent overlapping execution. Restart reconciliation exposes interrupted work for explicit resume from checkpoints.
- Review decisions persist before resume, so a crash between acceptance and resumption cannot lose the decision. Approval authorizes recommendations only; no remediation is executed.
- Mock metrics remain null for tokens/cost; wall-clock timings and tool counts are measured for both modes. Actual provider attempts are recorded individually, including failed attempts.
- External reputation providers require explicit opt-in. No indicator is contacted directly. Live smoke runs only when locally configured credentials and opt-in are present.
