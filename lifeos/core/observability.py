import json
import logging
import re
from contextlib import contextmanager
from time import perf_counter

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

tracer = trace.get_tracer("lifeos")
meter = metrics.get_meter("lifeos")
latency = meter.create_histogram("lifeos.operation.duration", unit="s")
counts = meter.create_counter("lifeos.operations")
tokens = meter.create_counter("lifeos.model.tokens")
costs = meter.create_counter("lifeos.model.estimated_cost", unit="USD")


def safe_error(error: Exception) -> str:
    # Provider exceptions can embed request URLs, tokens, payloads, and credentials.
    return type(error).__name__


class JsonFormatter(logging.Formatter):
    def format(self, record):
        message = re.sub(
            r"(?i)(bearer|password|api_key|token)[=: ]+[^ ,;]+",
            r"\1=[REDACTED]",
            record.getMessage(),
        )
        return json.dumps({"level": record.levelname, "logger": record.name, "message": message})


def setup(settings):
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(handlers=[handler], level=logging.INFO, force=True)
    # Never emit HTTP URLs, raw model data, or headers from dependency loggers.
    for name in ["httpx", "httpcore", "openai", "sqlalchemy.engine"]:
        logging.getLogger(name).setLevel(logging.WARNING)
    if settings.otel_exporter_otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        resource = Resource.create({"service.name": "lifeos-ai"})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        metrics.set_meter_provider(
            MeterProvider(
                resource=resource,
                metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())],
            )
        )


@contextmanager
def operation(name, **attributes):
    start = perf_counter()
    with tracer.start_as_current_span(
        name, attributes=attributes, record_exception=False, set_status_on_exception=False
    ) as span:
        try:
            yield
        except Exception as error:
            span.set_status(Status(StatusCode.ERROR, type(error).__name__))
            counts.add(1, {"operation": name, "status": "failed"})
            raise
        else:
            counts.add(1, {"operation": name, "status": "success"})
        finally:
            latency.record(perf_counter() - start, {"operation": name})
