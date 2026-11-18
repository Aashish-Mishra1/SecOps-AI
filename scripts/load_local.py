"""Authenticated loopback-only event-list benchmark, four concurrent clients."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
from urllib.parse import urlparse

from phase3_smoke import BASE, request


def main():
    if urlparse(BASE).hostname not in {"localhost", "127.0.0.1"}:
        raise SystemExit("Use a local Compose URL")
    token = request(
        "/auth/login",
        {"email": "analyst@secops.example", "password": os.environ["DEMO_PASSWORD"]},
    )["access_token"]

    def query(i):
        start = perf_counter()
        result = request("/events?limit=10&offset=" + str(i), token=token)
        assert isinstance(result["items"], list)
        return (perf_counter() - start) * 1000

    start = perf_counter()
    with ThreadPoolExecutor(4) as pool:
        times = sorted(pool.map(query, range(20)))
    print(
        json.dumps(
            {
                "requests": 20,
                "concurrency": 4,
                "requests_per_second": 20 / (perf_counter() - start),
                "p50_ms": times[9],
                "p95_ms": times[18],
            }
        )
    )


if __name__ == "__main__":
    main()
