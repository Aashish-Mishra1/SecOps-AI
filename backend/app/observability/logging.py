import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Deliberately allowlist fields; never serialize request bodies or exceptions.
        data = {
            "time": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        for name in (
            "request_id",
            "method",
            "status",
            "duration_ms",
            "investigation_id",
            "agent",
            "trace_id",
        ):
            if hasattr(record, name):
                data[name] = getattr(record, name)
        return json.dumps(data)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("secops")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
