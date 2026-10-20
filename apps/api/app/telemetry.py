"""Optional manual spans: no URLs, headers, query text, identities or payloads."""
from contextlib import contextmanager
from app.settings import read_local_config

_tracer = None


def setup():
    global _tracer
    config = read_local_config().get('telemetry', {})
    if not config.get('enabled', False):
        return
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    provider = TracerProvider(resource=Resource.create({'service.name': 'campus-api'}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
        endpoint=config.get('endpoint', 'http://127.0.0.1:4318/v1/traces'), timeout=2)))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer('campus')


@contextmanager
def span(name):
    if _tracer:
        with _tracer.start_as_current_span(name, record_exception=False, set_status_on_exception=False) as active:
            yield active
    else:
        yield None
