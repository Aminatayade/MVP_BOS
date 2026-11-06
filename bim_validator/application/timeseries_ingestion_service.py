"""
Couche APPLICATION
--------------------
Compose un MessageQueuePort (NATS) et un TimeseriesRepositoryPort (ArcadeDB)
injectés — même pattern que BimIngestionService/BimValidatorService.

Un message reçu ne doit JAMAIS faire planter le consommateur : tout rejet
(champ manquant, type invalide, global_id inconnu du graphe BIM) est loggé
et le message est simplement ignoré (pas d'écriture orpheline dans
EquipmentReading). Une exception qui remonterait jusqu'à l'adapter NATS
empêcherait l'ACK et provoquerait une re-livraison infinie du même message.
"""

from __future__ import annotations

from datetime import datetime

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from domain.entities import UnifiedMessage
from domain.ports import MessageQueuePort, TimeseriesRepositoryPort

REQUIRED_FIELDS = ("global_id", "timestamp", "metric", "value")
_tracer = trace.get_tracer("bos.timeseries.ingestion")


class TimeseriesIngestionService:
    def __init__(self, queue: MessageQueuePort, repository: TimeseriesRepositoryPort) -> None:
        self._queue = queue
        self._repository = repository

    def start_consuming(self, subject: str) -> None:
        self._queue.subscribe(subject, self._handle_message)

    def _handle_message(self, raw: dict) -> None:
        with _tracer.start_as_current_span("timeseries.ingest") as span:
            message = _parse_unified_message(raw)
            if message is None:
                span.set_status(Status(StatusCode.ERROR, "invalid telemetry message"))
                span.add_event("message.rejected", {"reason": "invalid_format"})
                print(f"⚠️  Message rejeté (format invalide) : {raw}")
                return

            span.set_attribute("telemetry.global_id", message.global_id)
            span.set_attribute("telemetry.metric", message.metric)
            span.set_attribute("telemetry.source", message.source)
            if not self._repository.equipment_exists(message.global_id):
                span.set_status(Status(StatusCode.ERROR, "unknown equipment"))
                span.add_event("message.rejected", {"reason": "unknown_equipment"})
                print(
                    f"⚠️  Message rejeté : global_id '{message.global_id}' inconnu du graphe BIM "
                    f"(aucun Equipment correspondant)."
                )
                return

            self._repository.write_point(message)
            span.add_event("measurement.persisted")
            print(
                f"✅ Point écrit : global_id={message.global_id} metric={message.metric} "
                f"value={message.value} ts={message.timestamp.isoformat()}"
            )


def _parse_unified_message(raw: dict) -> UnifiedMessage | None:
    if not isinstance(raw, dict):
        return None

    missing = [field for field in REQUIRED_FIELDS if field not in raw]
    if missing:
        print(f"⚠️  Champs obligatoires manquants {missing} dans le message : {raw}")
        return None

    try:
        timestamp = _parse_timestamp(raw["timestamp"])
        value = float(raw["value"])
        global_id = str(raw["global_id"])
        metric = str(raw["metric"])
    except (TypeError, ValueError) as exc:
        print(f"⚠️  Champ de type invalide dans le message {raw} : {exc}")
        return None

    if not global_id or not metric:
        print(f"⚠️  global_id ou metric vide dans le message : {raw}")
        return None

    return UnifiedMessage(
        global_id=global_id,
        timestamp=timestamp,
        metric=metric,
        value=value,
        unit=str(raw.get("unit", "")),
        quality=str(raw.get("quality", "good")),
        source=str(raw.get("source", "unknown")),
    )


def _parse_timestamp(raw_timestamp) -> datetime:
    if isinstance(raw_timestamp, datetime):
        return raw_timestamp
    return datetime.fromisoformat(str(raw_timestamp))
