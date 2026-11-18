# Phase 3 validation

Validated on 18 November 2026 in deterministic mock/offline mode. No Gemini, VirusTotal or AbuseIPDB call was made.

## Implemented workflow

`START -> supervisor -> [log_analysis | knowledge | threat_intel] -> remediation -> critic -> (targeted bounded retry | reporting) -> human review interrupt -> finalize -> END`

The three specialists execute concurrently in a LangGraph super-step with reducer-backed compact state and isolated SQLAlchemy sessions. PostgreSQL checkpoints use the investigation UUID as `thread_id`. Application records remain organization-scoped; checkpoint access is only reached through an authorized investigation.

Migration `0002_investigation_execution_and_metrics.py`:

- extends investigations with idempotency key, provider/model identity, execution timestamps, measured active time and tool/LLM budgets;
- extends agent runs with measured latency, prompt version and compact result;
- adds `tool_runs` and `llm_calls` with nullable usage/cost/TTFT fields;
- expands lifecycle status for recoverable interruptions and raises the bounded attempt constraint to three;
- leaves LangGraph checkpoint tables under the installed saver library's setup mechanism rather than Alembic.

## Actual checks

| Check | Result |
|---|---|
| Phase 1–2 regression before continuation | 28 passed in 20.89 s |
| Investigation lifecycle suite | 6 passed in 18.86 s |
| Agent security/control suite | 7 passed in 18.85 s |
| Final host backend suite | 41 passed in 41.93 s |
| Container Python 3.12 suite | 41 passed in 35.85 s |
| Ruff | All checks passed |
| Alembic drift | No new upgrade operations detected |
| Container build | Backend frozen uv sync and frontend npm/typecheck/Vite image build passed |
| Existing-volume HTTP smoke | Passed; 1008.58 ms active, 25 evidence, 7 tools, 11 mock calls |
| Fresh-volume Compose startup | Database, backend and frontend healthy; migrations/checkpoints/seed passed |
| Fresh-volume HTTP smoke | Passed; 1077.31 ms active, 25 evidence, 7 tools, 11 mock calls |

The test fixture creates an isolated database, upgrades to head, downgrades to base, reapplies migrations, checks drift, initializes checkpoints and removes only that test database.

## Offline evaluation

Five synthetic rule-aligned scenarios: brute force, suspicious successful authentication, potential exfiltration, privilege escalation and suspicious network traffic.

- deterministic single-agent rule baseline classification accuracy: 5/5;
- multi-agent report classification accuracy: 5/5;
- citation validity: 5/5;
- relevant-policy retrieval proxy: 5/5;
- baseline database/rule latency: 5.24–13.81 ms;
- multi-agent active execution: 1007.48–1706.81 ms;
- mock input/output tokens and cost: null, as unavailable.

These cases are generated to align with the deterministic rules. They verify plumbing, evidence grounding and repeatability; they do not establish real-world accuracy. Both comparison paths share the classification rules. Live-provider quality and human review remain untested.

## Sample persisted output

```json
{
  "provider": "mock",
  "model": "mock:agents-v1",
  "incident_classification": "brute_force",
  "severity": "high",
  "confidence": "medium",
  "completeness": "complete",
  "approval_status": "approved",
  "evidence_count": 25,
  "recommendations_require_approval": true,
  "actions_executed": false
}
```

## Remaining limits

- Local executor queue is not durable; checkpointed/database state is durable and orphaned running work becomes explicitly resumable.
- Live Gemini/Vertex and external reputation adapters are configuration- and contract-tested but not called.
- ATT&CK mappings are deterministic hypotheses, not confirmation of technique execution.
- Citation validation proves ownership, source existence and verbatim evidence support; it is not a general semantic-entailment proof.
- Advanced frontend dashboards, distributed workers, advanced tracing and cloud deployment remain outside Phase 3.
