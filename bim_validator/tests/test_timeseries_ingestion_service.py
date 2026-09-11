from application.timeseries_ingestion_service import TimeseriesIngestionService


class _FakeQueue:
    """N'implémente que ce que start_consuming() appelle -- pas besoin
    d'hériter de MessageQueuePort (Python ne vérifie pas le type ici)."""

    def subscribe(self, subject, handler):
        self.subject = subject
        self.handler = handler


class _FakeRepository:
    def __init__(self, *, equipment_known: bool = True):
        self._equipment_known = equipment_known
        self.write_point_calls: list = []

    def equipment_exists(self, global_id: str) -> bool:
        return self._equipment_known

    def write_point(self, message) -> None:
        self.write_point_calls.append(message)


VALID_MESSAGE = {
    "global_id": "eq-1",
    "timestamp": "2026-09-11T10:00:00+00:00",
    "metric": "temperature",
    "value": 21.5,
}


def test_handle_message_writes_exactly_one_point_per_call():
    """Verrouille l'hypothèse "un point par appel HTTP, aucun batching"
    dont dépend tout le calcul de cadence du simulateur live continu
    (live_simulation_service.py) : si ce comportement change (ex: un futur
    write_points_batch ici), la cadence par défaut (tick_seconds/
    cycle_minutes) doit être réexaminée, pas silencieusement invalidée."""
    repository = _FakeRepository()
    service = TimeseriesIngestionService(_FakeQueue(), repository)

    service._handle_message(VALID_MESSAGE)

    assert len(repository.write_point_calls) == 1
    written = repository.write_point_calls[0]
    assert written.global_id == "eq-1"
    assert written.metric == "temperature"
    assert written.value == 21.5


def test_handle_message_rejects_unknown_equipment_without_writing():
    repository = _FakeRepository(equipment_known=False)
    service = TimeseriesIngestionService(_FakeQueue(), repository)

    service._handle_message(VALID_MESSAGE)

    assert repository.write_point_calls == []


def test_handle_message_rejects_missing_required_field():
    repository = _FakeRepository()
    service = TimeseriesIngestionService(_FakeQueue(), repository)

    incomplete = {k: v for k, v in VALID_MESSAGE.items() if k != "value"}
    service._handle_message(incomplete)

    assert repository.write_point_calls == []
