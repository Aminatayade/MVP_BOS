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


def test_get_generator_spec_returns_energy_tags_for_chaudiere_fioul():
    catalog = _adapter()
    spec = catalog.get_generator_spec("chaudiere_fioul")
    assert spec.energy_subcategory == "cvc"
    assert spec.energy_type == "fioul"
    assert spec.history_days == 65


def test_get_generator_spec_leaves_energy_tags_none_for_untagged_type():
    catalog = _adapter()
    spec = catalog.get_generator_spec("automate_gtb")
    assert spec.energy_subcategory is None
    assert spec.energy_type is None


def test_list_energy_equipment_types_excludes_untagged_types():
    catalog = _adapter()
    types = catalog.list_energy_equipment_types()
    assert "automate_gtb" not in types
    assert "sonde_temperature" not in types
    assert "luminaire" in types
    assert "ascenseur" in types
    assert "chaudiere_fioul" in types


def test_list_energy_equipment_types_filters_by_subcategory():
    catalog = _adapter()
    types = catalog.list_energy_equipment_types(subcategory="cvc")
    assert set(types) == {
        "ventilo_convecteur", "cta", "groupe_froid", "ventilateur", "pompe",
        "cvc_generic", "compteur_thermique", "chaudiere_fioul",
    }


def test_list_energy_equipment_types_filters_by_energy_type():
    catalog = _adapter()
    types = catalog.list_energy_equipment_types(energy_type="fioul")
    assert types == ["chaudiere_fioul"]


def test_list_energy_equipment_types_combines_both_filters():
    catalog = _adapter()
    types = catalog.list_energy_equipment_types(subcategory="cvc", energy_type="electrique")
    assert "compteur_thermique" not in types
    assert "chaudiere_fioul" not in types
    assert "cta" in types
