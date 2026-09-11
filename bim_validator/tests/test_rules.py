from domain.entities import BimElement, Severity, ValidationIssue
from domain.rules import check_geometry_presence, check_required_property_sets


def test_check_geometry_presence_reports_elements_without_geometry():
    elements = [
        BimElement(
            global_id="1",
            ifc_type="IfcPipeSegment",
            express_id=100,
            is_linked_to_spatial_structure=True,
            property_sets={},
            has_geometry=False,
        ),
        BimElement(
            global_id="2",
            ifc_type="IfcPipeSegment",
            express_id=101,
            is_linked_to_spatial_structure=True,
            property_sets={},
            has_geometry=True,
        ),
    ]

    issues = check_geometry_presence(elements)

    assert len(issues) == 1
    assert issues[0].severity == Severity.WARNING
    assert "géométrie" in issues[0].message.lower()


def test_check_required_property_sets_applies_base_type_rule_to_standardcase_variant():
    # IfcWallStandardCase (très courant, ex: exports Revit) doit hériter des
    # exigences configurées sous "IfcWall" dans la charte -- sans repli, ces
    # murs échappaient silencieusement à toute vérification (constaté sur
    # une vraie maquette : 1065 murs sur 1080 jamais contrôlés).
    element = BimElement(
        global_id="1" * 22,
        ifc_type="IfcWallStandardCase",
        express_id=1,
        is_linked_to_spatial_structure=True,
        property_sets={},
        has_geometry=True,
    )
    required = {"IfcWall": ["Pset_WallCommon.FireRating"]}

    issues = check_required_property_sets([element], required)

    assert len(issues) == 1
    assert issues[0].severity == Severity.ERROR
    assert "FireRating" in issues[0].message


def test_check_required_property_sets_base_type_rule_satisfied_by_standardcase_variant():
    element = BimElement(
        global_id="1" * 22,
        ifc_type="IfcWallStandardCase",
        express_id=1,
        is_linked_to_spatial_structure=True,
        property_sets={"Pset_WallCommon": {"FireRating": "REI 60"}},
        has_geometry=True,
    )
    required = {"IfcWall": ["Pset_WallCommon.FireRating"]}

    issues = check_required_property_sets([element], required)

    assert issues == []
