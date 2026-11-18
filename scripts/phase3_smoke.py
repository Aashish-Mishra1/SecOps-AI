"""End-to-end mock investigation through the frontend reverse proxy."""

import json
import os
import time
import urllib.request
import uuid

BASE = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:5173") + "/api/v1"


def request(path, body=None, token=None, method=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def wait_for(identity, token, statuses):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        result = request(f"/investigations/{identity}", token=token)
        if result["status"] in statuses:
            return result
        time.sleep(0.2)
    raise TimeoutError("Investigation did not reach the expected state")


def main():
    password = os.environ["DEMO_PASSWORD"]
    analyst = request("/auth/login", {"email": "analyst@secops.example", "password": password})[
        "access_token"
    ]
    reviewer = request("/auth/login", {"email": "reviewer@secops.example", "password": password})[
        "access_token"
    ]
    alerts = request("/alerts?limit=100", token=analyst)["items"]
    alert = next(item for item in alerts if item["title"] == "Repeated authentication failures")
    created = request(
        "/investigations",
        {"alert_id": alert["id"], "request_id": str(uuid.uuid4()), "auto_start": True},
        analyst,
    )
    draft = wait_for(created["id"], analyst, {"awaiting_review", "failed"})
    if draft["status"] != "awaiting_review":
        raise RuntimeError(f"Investigation failed: {draft['error_code']}")
    report = request(f"/investigations/{created['id']}/report", token=analyst)
    evidence = request(f"/investigations/{created['id']}/evidence?limit=100", token=analyst)["items"]
    evidence_ids = {item["id"] for item in evidence}
    if not set(report["evidence_ids"]).issubset(evidence_ids):
        raise RuntimeError("Report contains unavailable citations")
    request(
        f"/investigations/{created['id']}/review",
        {"decision": "approve", "comment": "Synthetic local demo approval; no action executed."},
        reviewer,
    )
    final = wait_for(created["id"], analyst, {"approved", "rejected", "failed"})
    metrics = request(f"/investigations/{created['id']}/metrics", token=analyst)
    if final["status"] != "approved" or final["report"]["approval_status"] != "approved":
        raise RuntimeError(f"Review resume failed: {final['status']}")
    print(
        json.dumps(
            {
                "status": "passed",
                "investigation_id": created["id"],
                "classification": report["incident_classification"],
                "severity": report["severity"],
                "completeness": report["completeness"],
                "evidence_count": len(evidence),
                "tool_invocations": metrics["tool_invocations"],
                "llm_invocations": metrics["llm_invocations"],
                "active_execution_ms": metrics["active_execution_ms"],
                "input_tokens": metrics["input_tokens"],
                "cost_usd": metrics["cost_usd"],
                "provider": metrics["provider"],
            }
        )
    )


if __name__ == "__main__":
    main()
