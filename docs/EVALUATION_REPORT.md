# Deterministic investigation evaluation

Run `docker compose run --rm backend uv run --frozen pytest -qs tests/test_evaluation.py`. The fixture creates a disposable PostgreSQL database and a fresh tenant. Each arm reads the same alert, events and corpus under the same mock provider. The baseline executes the real log-analysis specialist in isolation. The multi-agent arm runs the whole graph to its human-review interrupt.

Labels cover brute force, unusual successful authentication, possible exfiltration, privilege escalation, suspicious network traffic, routine maintenance, and brute force with an instruction-bearing document in the index. Routine maintenance maps to activity_for_review. The response playbook is the labeled relevant retrieval document. Precision@1 uses the first hit of an independent hybrid query with the alert title.

| Seven cases | Single specialist | Full workflow |
|---|---:|---:|
| Classification accuracy | 1.0 | 1.0 |
| Macro precision / recall / F1 | 1.0 / 1.0 / 1.0 | 1.0 / 1.0 / 1.0 |
| Active latency P50 / P95, ms | 225.96 / 327.74 | 678.47 / 964.54 |

All seven workflows reached pending review. Every report had citations that passed the scoped source validator. Retrieval precision@1 was 1.0. Both arms share deterministic classification rules. The sample measures wiring and behavior on synthetic fixtures. Generalization, semantic entailment and live-provider quality require independent grading.
