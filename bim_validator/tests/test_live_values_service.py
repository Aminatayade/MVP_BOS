from application.live_values_service import diff_values, load_values_file, parse_values, resolve_equipment_ids

METRICS = {"power_instant_kw", "temperature", "debit_instant_m3h"}


# --- load_values_file ---------------------------------------------------

def test_load_values_file_reads_a_valid_file(tmp_path):
    fichier = tmp_path / "v.json"
    fichier.write_text('{"zone": "Batipompe", "valeurs": []}', encoding="utf-8")
    data, error = load_values_file(str(fichier))
    assert error is None
    assert data == {"zone": "Batipompe", "valeurs": []}


def test_load_values_file_missing_file_reports_error_not_exception():
    data, error = load_values_file("/chemin/qui/n_existe_pas.json")
    assert data is None
    assert "introuvable" in error


def test_load_values_file_empty_file_reports_error():
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        path = f.name
    data, error = load_values_file(path)
    assert data is None
    assert "vide" in error


def test_load_values_file_invalid_json_reports_error_not_exception(tmp_path):
    fichier = tmp_path / "v.json"
    fichier.write_text('{"zone": "A", "valeurs": [', encoding="utf-8")
    data, error = load_values_file(str(fichier))
    assert data is None
    assert "JSON invalide" in error


def test_load_values_file_rejects_a_json_array_at_top_level(tmp_path):
    fichier = tmp_path / "v.json"
    fichier.write_text("[1, 2, 3]", encoding="utf-8")
    data, error = load_values_file(str(fichier))
    assert data is None
    assert "objet JSON" in error


def test_load_values_file_ignores_a_leading_bom(tmp_path):
    # Un éditeur Windows peut ajouter ce caractère invisible en tête de
    # fichier -- il ne doit jamais faire échouer un JSON par ailleurs valide.
    fichier = tmp_path / "v.json"
    fichier.write_bytes(b"\xef\xbb\xbf" + b'{"zone": "Z", "valeurs": []}')
    data, error = load_values_file(str(fichier))
    assert error is None
    assert data == {"zone": "Z", "valeurs": []}


# --- parse_values ---------------------------------------------------------

def test_parse_values_accepts_a_valid_line():
    raw = {"zone": "Batipompe", "valeurs": [
        {"equipement": "Gaine 1", "metrique": "power_instant_kw", "valeur": 6.0}]}
    result = parse_values(raw, METRICS)
    assert result.zone == "Batipompe"
    assert result.values == {("Gaine 1", "power_instant_kw"): 6.0}
    assert result.errors == []


def test_parse_values_missing_zone_reports_error():
    result = parse_values({"valeurs": []}, METRICS)
    assert result.values == {}
    assert "zone" in result.errors[0]


def test_parse_values_valeurs_must_be_a_list():
    result = parse_values({"zone": "A", "valeurs": {"pas": "une liste"}}, METRICS)
    assert result.values == {}
    assert "liste" in result.errors[0]


def test_parse_values_rejects_non_numeric_value_but_keeps_other_lines():
    raw = {"zone": "A", "valeurs": [
        {"equipement": "a", "metrique": "power_instant_kw", "valeur": "abc"},
        {"equipement": "b", "metrique": "power_instant_kw", "valeur": 3.0},
    ]}
    result = parse_values(raw, METRICS)
    assert result.values == {("b", "power_instant_kw"): 3.0}
    assert len(result.errors) == 1
    assert "nombre" in result.errors[0]


def test_parse_values_rejects_a_boolean_value():
    # True == 1 en Python : sans ce garde-fou, "valeur": true serait
    # silencieusement accepté comme la valeur numérique 1.
    raw = {"zone": "A", "valeurs": [
        {"equipement": "a", "metrique": "power_instant_kw", "valeur": True}]}
    result = parse_values(raw, METRICS)
    assert result.values == {}
    assert "nombre" in result.errors[0]


def test_parse_values_rejects_unknown_metric():
    raw = {"zone": "A", "valeurs": [
        {"equipement": "a", "metrique": "metrique_qui_n_existe_pas", "valeur": 1.0}]}
    result = parse_values(raw, METRICS)
    assert result.values == {}
    assert "métrique inconnue" in result.errors[0]


def test_parse_values_rejects_missing_equipement():
    raw = {"zone": "A", "valeurs": [
        {"equipement": "", "metrique": "power_instant_kw", "valeur": 1.0}]}
    result = parse_values(raw, METRICS)
    assert result.values == {}
    assert "equipement" in result.errors[0]


def test_parse_values_rejects_duplicate_line_keeps_first():
    raw = {"zone": "A", "valeurs": [
        {"equipement": "a", "metrique": "power_instant_kw", "valeur": 1.0},
        {"equipement": "a", "metrique": "power_instant_kw", "valeur": 2.0},
    ]}
    result = parse_values(raw, METRICS)
    assert result.values == {("a", "power_instant_kw"): 1.0}
    assert "doublon" in result.errors[0]


def test_parse_values_rejects_a_non_object_line():
    raw = {"zone": "A", "valeurs": ["ceci n'est pas un objet"]}
    result = parse_values(raw, METRICS)
    assert result.values == {}
    assert "objet" in result.errors[0]


def test_parse_values_real_file_matches_expected_shape():
    # Reproduit live/valeurs.json (Batipompe, 4 lignes valides) -- garde-fou
    # contre une régression sur le fichier réellement utilisé en démo.
    raw = {
        "zone": "Batipompe",
        "valeurs": [
            {"equipement": "Gaine circulaire:VEN_GAI_Gaine circulaire:5938661",
             "metrique": "power_instant_kw", "valeur": 8.0},
            {"equipement": "Gaine circulaire:VEN_GAI_Gaine circulaire:5939283",
             "metrique": "power_instant_kw", "valeur": 4.5},
            {"equipement": "Canalisation:ARC-CAN_Tuyaux:8051296",
             "metrique": "debit_instant_m3h", "valeur": 120.0},
            {"equipement": "SAN_APS_Chauffe-eau instantané:MCX3:6358042",
             "metrique": "temperature", "valeur": 65.0},
        ],
    }
    result = parse_values(raw, METRICS)
    assert result.errors == []
    assert len(result.values) == 4


# --- diff_values ------------------------------------------------------------

def test_diff_values_detects_changed_added_removed_unchanged():
    old = {("A", "m"): 1.0, ("B", "m"): 2.0, ("C", "m"): 3.0}
    new = {("A", "m"): 1.0, ("B", "m"): 9.0, ("D", "m"): 5.0}
    diff = diff_values(old, new)
    assert diff.changed == {("B", "m"): 9.0, ("D", "m"): 5.0}
    assert diff.removed == [("C", "m")]
    assert diff.unchanged == [("A", "m")]


def test_diff_values_first_run_everything_is_changed():
    diff = diff_values({}, {("A", "m"): 1.0})
    assert diff.changed == {("A", "m"): 1.0}
    assert diff.removed == []
    assert diff.unchanged == []


def test_diff_values_identical_versions_nothing_changed():
    values = {("A", "m"): 1.0, ("B", "m"): 2.0}
    diff = diff_values(values, dict(values))
    assert diff.changed == {}
    assert diff.removed == []
    assert sorted(diff.unchanged) == [("A", "m"), ("B", "m")]


# --- resolve_equipment_ids ---------------------------------------------

METRICS_BY_TYPE = {"vanne_arret": {"state"}, "cvc_generic": {"power_instant_kw", "temperature"}}


def test_resolve_equipment_ids_happy_path():
    values = {("Robinet A", "state"): 1.0}
    equipment_by_name = {"Robinet A": [("gid-1", "vanne_arret")]}
    result = resolve_equipment_ids(values, equipment_by_name, METRICS_BY_TYPE)
    assert result.errors == []
    assert result.resolved == [
        __import__("application.live_values_service", fromlist=["ResolvedValue"]).ResolvedValue(
            global_id="gid-1", equipment_type="vanne_arret", metric="state", value=1.0)
    ]


def test_resolve_equipment_ids_rejects_unknown_name():
    values = {("Robinet fantôme", "state"): 1.0}
    result = resolve_equipment_ids(values, {}, METRICS_BY_TYPE)
    assert result.resolved == []
    assert "introuvable" in result.errors[0]


def test_resolve_equipment_ids_rejects_ambiguous_name():
    # Reproduit le cas réel rencontré en test manuel : deux robinets
    # presque identiques, seul le numéro final (non inclus ici) diffère --
    # doit être refusé, jamais résolu "au hasard" vers le premier trouvé.
    values = {("RN 40210 Robinet de jardin hors gel", "state"): 1.0}
    equipment_by_name = {
        "RN 40210 Robinet de jardin hors gel": [
            ("gid-1", "vanne_arret"), ("gid-2", "vanne_arret"),
        ]
    }
    result = resolve_equipment_ids(values, equipment_by_name, METRICS_BY_TYPE)
    assert result.resolved == []
    assert "ambigu" in result.errors[0]


def test_resolve_equipment_ids_rejects_metric_not_produced_by_this_type():
    values = {("Robinet A", "power_instant_kw"): 5.0}
    equipment_by_name = {"Robinet A": [("gid-1", "vanne_arret")]}
    result = resolve_equipment_ids(values, equipment_by_name, METRICS_BY_TYPE)
    assert result.resolved == []
    assert "ne produit pas" in result.errors[0]


def test_resolve_equipment_ids_partial_failure_keeps_valid_lines():
    values = {
        ("Robinet A", "state"): 1.0,
        ("Robinet fantôme", "state"): 1.0,
    }
    equipment_by_name = {"Robinet A": [("gid-1", "vanne_arret")]}
    result = resolve_equipment_ids(values, equipment_by_name, METRICS_BY_TYPE)
    assert len(result.resolved) == 1
    assert result.resolved[0].global_id == "gid-1"
    assert len(result.errors) == 1
