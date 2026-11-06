"""OpenTelemetry setup shared by the API gateway process."""

from __future__ import annotations

import os
from functools import lru_cache

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


@lru_cache(maxsize=1)
def configure_tracing() -> None:
    """Configure OTLP export and instrument FastAPI once per process."""
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return

    resource = Resource.create(
        {
            "service.name": os.getenv("OTEL_SERVICE_NAME", "bos-api-gateway"),
            "service.version": os.getenv("APP_VERSION", "dev"),
            "deployment.environment": os.getenv("OTEL_ENVIRONMENT", "development"),
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=endpoint,
                insecure=os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "true").lower() == "true",
            )
        )
    )
    trace.set_tracer_provider(provider)


def instrument_fastapi(app) -> None:
    """Instrument an application after the provider has been configured."""
    configure_tracing()
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        FastAPIInstrumentor.instrument_app(app)
