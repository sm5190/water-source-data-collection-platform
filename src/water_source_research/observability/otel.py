from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from water_source_research.core.config import get_settings

def configure_otel() -> None:
    s = get_settings()
    resource = Resource.create({"service.name": s.otel_service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=s.otel_exporter_otlp_endpoint, insecure=True)
        )
    )
    trace.set_tracer_provider(provider)
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=s.otel_exporter_otlp_endpoint, insecure=True)
    )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))

def get_tracer():
    return trace.get_tracer("water_source_research")

def get_meter():
    return metrics.get_meter("water_source_research")
