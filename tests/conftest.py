import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_EXPORTER = InMemorySpanExporter()
_METRIC_READER = InMemoryMetricReader()

@pytest.fixture(scope="session")
def _tracer_provider():
    # The global providers can only be set once per process.
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
    trace.set_tracer_provider(provider)
    metrics.set_meter_provider(MeterProvider(metric_readers=[_METRIC_READER]))
    return provider

@pytest.fixture
def spans(_tracer_provider):
    _EXPORTER.clear()
    yield _EXPORTER
    _EXPORTER.clear()

@pytest.fixture
def metric_points(_tracer_provider):
    """Callable returning {metric_name: [(attributes, value)]} accumulated so far."""
    def collect():
        out: dict[str, list] = {}
        data = _METRIC_READER.get_metrics_data()
        for rm in (data.resource_metrics if data else []):
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    for p in m.data.data_points:
                        value = getattr(p, "value", None)
                        if value is None:
                            value = getattr(p, "count", None)
                        out.setdefault(m.name, []).append((dict(p.attributes), value))
        return out
    return collect
