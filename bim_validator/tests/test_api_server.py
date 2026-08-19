from domain.entities import Severity, ValidationIssue, ValidationReport
from api_server import serialize_validation_report


def test_serialize_validation_report_includes_errors_and_warnings():
    report = ValidationReport(
        success=False,
        status=422,
        issues=[
            ValidationIssue(severity=Severity.ERROR, code="MISSING_PSET", message="Pset manquant"),
            ValidationIssue(severity=Severity.WARNING, code="NAME_MISSING", message="Nom manquant"),
        ],
    )

    payload = serialize_validation_report(report)

    assert payload["success"] is False
    assert payload["status"] == 422
    assert payload["errors"] == ["Pset manquant"]
    assert payload["warnings"] == ["Nom manquant"]
