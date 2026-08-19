import os

from adapters.json_rules_adapter import JsonRulesRepositoryAdapter

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "charte_bim_rules.json")


def _adapter() -> JsonRulesRepositoryAdapter:
    return JsonRulesRepositoryAdapter(CONFIG_PATH)


def test_canonical_discipline_resolves_alias_and_normalizes_case():
    repo = _adapter()
    assert repo.canonical_discipline("archi") == "ARCHI"
    assert repo.canonical_discipline("Architecture") == "ARCHI"
    assert repo.canonical_discipline("sanitaire") == "PLOMBERIE"


def test_is_spatial_backbone_discipline_true_only_for_archi_variants():
    repo = _adapter()
    assert repo.is_spatial_backbone_discipline("ARCHI") is True
    assert repo.is_spatial_backbone_discipline("architecture") is True
    assert repo.is_spatial_backbone_discipline("CVC") is False
    assert repo.is_spatial_backbone_discipline("ELEC") is False
    assert repo.is_spatial_backbone_discipline("PLOMBERIE") is False


def test_is_spatial_backbone_discipline_raises_for_unknown_discipline():
    repo = _adapter()
    try:
        repo.is_spatial_backbone_discipline("DISCIPLINE_INCONNUE")
        assert False, "expected KeyError"
    except KeyError:
        pass
