"""Run through the frontend reverse proxy; uses only synthetic local data."""
import json
import os
import urllib.error
import urllib.request


def main() -> None:
    base = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:5173") + "/api/v1"

    def request(path, body=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)

    assert request("/health/ready")["status"] == "ready"
    login = request("/auth/login", {"email": "analyst@secops.example", "password": os.environ["DEMO_PASSWORD"]})
    token = login["access_token"]
    events = request("/events?username=demo-analyst-01", token=token)
    assert events["total"] == 151
    assert request("/alerts", token=token)["total"] >= 4
    result = request("/rag/query", {"query": "MFA active sessions", "mode": "hybrid", "top_k": 3}, token)
    assert result["sources"] and result["provider"] == "mock"
    assert all(source["chunk_id"] in result["answer"] for source in result["sources"])
    assert result["generation_output_tokens"] is None
    print(json.dumps({"status": "passed", "event_count": events["total"], "cited_sources": len(result["sources"]),
                      "retrieval_latency_ms": result["retrieval_latency_ms"], "provider": result["provider"]}))


if __name__ == "__main__":
    main()
