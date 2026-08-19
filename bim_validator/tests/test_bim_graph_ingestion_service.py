from application.services.bim_graph_ingestion_service import (
    BimGraphIngestionResult,
    _cascade_display_name,
    _merge_spatial_node_query,
    _raw_name,
    _resolve_room_typology,
    _to_spatial_node,
    SpatialNode,
)


def test_ingestion_result_defaults_equipment_counters_to_zero():
    result = BimGraphIngestionResult(
        status="ingested", message="ok", source_key="key", validation=None
    )
    assert result.equipment_count == 0
    assert result.unmatched_equipment_count == 0


def test_merge_spatial_node_query_uses_property_map_upsert_for_building():
    query = _merge_spatial_node_query(
        SpatialNode(
            label="Building",
            global_id="3eM8WbY_59RR5TDWry5aRU",
            name="Main Building",
            raw_name="BAT-01",
            discipline="ARCHI",
            ifc_type="IfcBuilding",
            storage_url="http://storage.local/models/building.ifc",
            zone="Batiment Principal",
        )
    )

    assert query == (
        "MERGE (n:Building {global_id: '3eM8WbY_59RR5TDWry5aRU'})"
        " ON CREATE SET n += {name: 'Main Building', raw_name: 'BAT-01', discipline: 'ARCHI', zone: 'Batiment Principal', ifc_type: 'IfcBuilding', storage_url: 'http://storage.local/models/building.ifc'}"
        " ON MATCH SET n += {name: 'Main Building', raw_name: 'BAT-01', discipline: 'ARCHI', zone: 'Batiment Principal', ifc_type: 'IfcBuilding', storage_url: 'http://storage.local/models/building.ifc'}"
    )


def test_to_spatial_node_falls_back_to_label_when_name_is_missing():
    class DummyIfcEntity:
        GlobalId = "abc123"
        Name = None

        def is_a(self):
            return "IfcBuilding"

    node = _to_spatial_node(DummyIfcEntity(), "Building", discipline="ARCHI", storage_url="storage://x")

    assert node.name == "Building"
    assert node.raw_name == ""


def test_cascade_display_name_prefers_long_name_over_name():
    class DummyIfcSpace:
        LongName = "Bureau"
        Name = "101"

    assert _cascade_display_name(DummyIfcSpace(), fallback="Room") == "Bureau"
    assert _raw_name(DummyIfcSpace()) == "101"


def test_cascade_display_name_falls_back_to_object_type_then_tag():
    class NoLongNameNoName:
        LongName = None
        Name = None
        ObjectType = "Diffuseur d'air"

    assert _cascade_display_name(NoLongNameNoName(), fallback="Equipment") == "Diffuseur d'air"

    class OnlyTag:
        Tag = "EQ-42"

    assert _cascade_display_name(OnlyTag(), fallback="Equipment") == "EQ-42"


def test_resolve_room_typology_uses_specific_occupancy_type_when_available():
    # raw_room=None => _get_occupancy_type renvoie "" => on retombe sur les mots-clés.
    assert _resolve_room_typology(None, name="Salle serveur", raw_name="LT-01") == "technical"


def test_resolve_room_typology_falls_back_to_keywords_on_generic_value_and_searches_raw_name_too():
    # Le mot-clé n'est présent que dans raw_name ("WC-101"), pas dans name ("Local 101") :
    # la recherche combinée raw_name+name doit quand même le trouver.
    assert _resolve_room_typology(None, name="Local 101", raw_name="WC-101") == "sanitary"


def test_resolve_room_typology_defaults_to_other_when_nothing_matches():
    assert _resolve_room_typology(None, name="Local 999", raw_name="999") == "other"
