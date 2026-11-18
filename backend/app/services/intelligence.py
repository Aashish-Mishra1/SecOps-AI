"""Read-only reputation adapters. Fixed provider origins; never contact the indicator itself."""

from ipaddress import ip_address
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.errors import AppError

MITRE = {
    "brute_force": ("T1110", "Brute Force"),
    "suspicious_authentication": ("T1078", "Valid Accounts"),
    "potential_exfiltration": ("T1048", "Exfiltration Over Alternative Protocol"),
    "privilege_escalation": ("T1098", "Account Manipulation"),
    "suspicious_network": ("T1071", "Application Layer Protocol"),
}


def external_lookup(kind: str, value: str, client: httpx.Client | None = None) -> dict:
    settings = get_settings()
    provider = settings.threat_intel_provider
    if not settings.allow_external_intel or provider == "offline":
        raise AppError(403, "external_disabled", "External intelligence is disabled")
    # Reserved/private addresses and synthetic domains never leave the local environment.
    if kind == "ip" and not ip_address(value).is_global:
        return {
            "classification": "unknown",
            "source": provider,
            "mode": "not_queried",
            "limitation": "Non-public or documentation address was not sent externally.",
        }
    if kind == "domain" and (
        value.endswith((".example", ".test", ".invalid", ".localhost"))
        or value in {"example.com", "example.org", "example.net"}
    ):
        return {
            "classification": "unknown",
            "source": provider,
            "mode": "not_queried",
            "limitation": "Synthetic domain was not sent externally.",
        }
    if provider == "abuseipdb" and kind != "ip":
        return {
            "classification": "unknown",
            "source": provider,
            "mode": "not_queried",
            "limitation": "AbuseIPDB supports IP addresses only.",
        }
    owned = client is None
    client = client or httpx.Client(
        timeout=min(settings.tool_timeout_seconds, settings.provider_timeout_seconds),
        follow_redirects=False,
        trust_env=False,
    )
    try:
        if provider == "virustotal":
            resource = {"ip": "ip_addresses", "domain": "domains", "sha256": "files"}[kind]
            response = client.get(
                f"https://www.virustotal.com/api/v3/{resource}/{quote(value, safe='')}",
                headers={"x-apikey": settings.virustotal_api_key.get_secret_value()},
            )
            if response.status_code == 404:
                return {
                    "classification": "unknown",
                    "source": provider,
                    "mode": "live",
                    "limitation": "No provider record.",
                }
            response.raise_for_status()
            stats = response.json()["data"]["attributes"].get("last_analysis_stats", {})
            malicious, suspicious = int(stats.get("malicious", 0)), int(stats.get("suspicious", 0))
            classification = "malicious" if malicious else "suspicious" if suspicious else "unknown"
            return {
                "classification": classification,
                "source": provider,
                "mode": "live",
                "malicious_votes": malicious,
                "suspicious_votes": suspicious,
                "limitation": "Reputation votes are not proof of compromise; no detections does not mean benign.",
            }
        response = client.get(
            "https://api.abuseipdb.com/api/v2/check",
            params={"ipAddress": value, "maxAgeInDays": 90},
            headers={"Key": settings.abuseipdb_api_key.get_secret_value(), "Accept": "application/json"},
        )
        response.raise_for_status()
        score = int(response.json()["data"]["abuseConfidenceScore"])
        return {
            "classification": "suspicious" if score >= 50 else "unknown",
            "source": provider,
            "mode": "live",
            "provider_score": score,
            "limitation": "Provider score is not a calibrated incident probability.",
        }
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise AppError(
            502, "intel_provider_error", "External reputation provider failed; no fallback was used"
        ) from None
    finally:
        if owned:
            client.close()
