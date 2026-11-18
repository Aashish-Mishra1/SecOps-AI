"""Local OpenTelemetry spans omit payloads and exception messages."""

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

from app.config import get_settings

provider = TracerProvider()
if get_settings().trace_console:
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
tracer = provider.get_tracer("secops")


def span(name: str):
    return tracer.start_as_current_span(name, record_exception=False, set_status_on_exception=False)
