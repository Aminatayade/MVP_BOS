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


def test_is_spatial_backbone_discipline_true_for_archi_cvc_structure_variants():
    # ARCHI/ARCHITECTURE, CVC et STRUCTURE définissent le backbone spatial
    # (CVC et STRUCTURE activés en session pour des maquettes industrielles
    # sans ARCHI -- voir charte_bim_rules.json) ; ELEC/PLOMBERIE non.
    repo = _adapter()
    assert repo.is_spatial_backbone_discipline("ARCHI") is True
    assert repo.is_spatial_backbone_discipline("architecture") is True
    assert repo.is_spatial_backbone_discipline("CVC") is True
    assert repo.is_spatial_backbone_discipline("STRUCTURE") is True
    assert repo.is_spatial_backbone_discipline("ELEC") is False
    assert repo.is_spatial_backbone_discipline("PLOMBERIE") is False


def test_is_spatial_backbone_discipline_raises_for_unknown_discipline():
    repo = _adapter()
    try:
        repo.is_spatial_backbone_discipline("DISCIPLINE_INCONNUE")
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_canonical_disciplines_dedupes_resolves_aliases_and_sorts():
    repo = _adapter()
    assert repo.canonical_disciplines(["cvc", "electricite", "CVC"]) == ["CVC", "ELEC"]


def test_is_spatial_backbone_disciplines_true_if_any_discipline_is_backbone():
    repo = _adapter()
    assert repo.is_spatial_backbone_disciplines(["ELEC", "STRUCTURE"]) is True
    assert repo.is_spatial_backbone_disciplines(["ELEC", "PLOMBERIE"]) is False


def test_get_business_critical_types_union_merges_without_duplicates():
    repo = _adapter()
    union = repo.get_business_critical_types_union(["CVC", "ELEC"])
    assert "IfcAirTerminal" in union  # spécifique à CVC
    assert "IfcOutlet" in union  # spécifique à ELEC
    assert len(union) == len(set(union))  # pas de doublons


def test_get_required_property_sets_union_merges_requirements_per_ifc_type():
    repo = _adapter()
    union = repo.get_required_property_sets_union(["CVC", "MEP"])
    # IfcPipeSegment n'existe que dans MEP -> doit apparaître
    assert "Pset_PipeSegmentTypeCommon.NominalDiameter" in union.get("IfcPipeSegment", [])
    # IfcAirTerminal n'existe que dans CVC -> doit apparaître aussi
    assert "Pset_AirTerminalTypeCommon.AirFlowrateRange" in union.get("IfcAirTerminal", [])


def test_get_required_spatial_elements_union_combines_distinct_requirements():
    repo = _adapter()
    # PLOMBERIE exige seulement IfcSite ; CVC exige Building+Storey -- combinés,
    # les trois doivent être requis.
    union = repo.get_required_spatial_elements_union(["PLOMBERIE", "CVC"])
    assert set(union) == {"IfcSite", "IfcBuilding", "IfcBuildingStorey"}
