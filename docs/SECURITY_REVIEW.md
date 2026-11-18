# Local security review

| Area | Controls and evidence |
|---|---|
| Authorization | API derives tenant and role from authenticated identity. Existing tests exercise cross-tenant lookup, analyst review denial and scoped evidence. The browser test signs in separately for each role. |
| Prompt injection | Retrieved text remains data. Structured schemas, read-only tool allowlists and citation validation constrain findings. The malicious-policy fixture tests pending review and unchanged analyst privilege. |
| Input validation | API validates uploads, file size, parsing, IP/time filters, pagination and review decisions. React renders source strings as text. |
| Secrets and traces | Session token stays in browser memory. Worker credentials come from local environment. Trace attributes contain the investigation UUID. The trace test inspects exported attributes across nested calls. |
| Recovery | Database advisory lock serializes execution per investigation. Checkpoints and persisted review decisions survive worker loss. Redelivery is capped and external remediation stays human-controlled. |

Remaining risks include application-level tenant isolation, per-process rate limiting, repeated provider billing if a process exits before checkpointing, and hostile-file parser exposure. Four high-severity development dependency findings appeared during npm installation. Live-provider security and penetration testing remain outside the measured checks.
