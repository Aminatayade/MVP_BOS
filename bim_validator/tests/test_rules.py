from domain.entities import BimElement, Severity, ValidationIssue
from domain.rules import check_geometry_presence


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
    assert issues[0].severity == Severity.ERROR
    assert "géométrie" in issues[0].message.lower()
