import os

from adapters.json_equipment_catalog_adapter import JsonEquipmentCatalogAdapter

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "equipment_simulation_catalog.json")


def _adapter() -> JsonEquipmentCatalogAdapter:
    return JsonEquipmentCatalogAdapter(CONFIG_PATH)


def test_classify_resolves_via_predefined_type_first():
    catalog = _adapter()
    equipment_type = catalog.classify(
        ifc_type="IfcSensor",
        predefined_type="TEMPERATURESENSOR",
        category="cvc",
        name="Capteur générique sans mot-clé utile",
        raw_name="X-01",
        object_type="",
    )
    assert equipment_type == "sonde_temperature"


def test_classify_falls_back_to_keyword_when_predefined_type_unset():
    catalog = _adapter()
    equipment_type = catalog.classify(
        ifc_type="IfcSensor",
        predefined_type="NOTDEFINED",
        category="cvc",
        name="Sonde Temp Bureau 204",
        raw_name="204-T",
        object_type="",
    )
    assert equipment_type == "sonde_temperature"


def test_classify_falls_back_to_broad_category_when_nothing_matches():
    catalog = _adapter()
    # Reproduit le cas réel des diffuseurs "Monsun" : aucun signal exploitable.
    equipment_type = catalog.classify(
        ifc_type="IfcFlowTerminal",
        predefined_type=None,
        category="cvc",
        name="Monsun:Monsun:155591",
        raw_name="Monsun:Monsun:155591",
        object_type="Monsun:Monsun",
    )
    assert equipment_type == "cvc_generic"


def test_classify_unknown_category_returns_unknown_marker():
    catalog = _adapter()
    equipment_type = catalog.classify(
        ifc_type="IfcProxy",
        predefined_type=None,
        category="does_not_exist",
        name="",
        raw_name="",
        object_type="",
    )
    assert equipment_type == "_unknown"


def test_get_generator_spec_for_unknown_has_no_metrics():
    catalog = _adapter()
    spec = catalog.get_generator_spec("_unknown")
    assert spec.metrics == []


def test_get_generator_spec_returns_configured_metrics():
    catalog = _adapter()
    spec = catalog.get_generator_spec("compteur_electrique")
    metric_names = {m.name for m in spec.metrics}
    assert metric_names == {"energy_cumulative_kwh", "power_instant_kw"}
    assert spec.history_days == 7
