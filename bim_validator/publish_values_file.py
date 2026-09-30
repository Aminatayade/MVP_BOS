"""
Script utilitaire : rejoue live/valeurs.json vers les équipements réels
d'une zone -- publie CHAQUE ligne valide UNE FOIS sur le même canal NATS que
la simulation live (voir simulate_live_stream.py), avec la valeur EXACTE du
fichier au lieu d'une valeur générée aléatoirement par le catalogue.

Usage :
    python publish_values_file.py
    python publish_values_file.py --fichier live/valeurs.json
    python publish_values_file.py --dry-run   # valide et affiche, ne publie rien

Contrairement à simulate_live_stream.py, ce script ne tourne PAS en boucle :
il publie une fois puis s'arrête. Relance-le à chaque fois que tu modifies
live/valeurs.json pour voir l'effet dans les pop-ups de télémétrie (3D ou
graphe) du Configurateur.

N'écrit jamais si le fichier est invalide ou vide -- voir
application/live_values_service.py pour le détail des règles de validation
(nom d'équipement inconnu ou ambigu, métrique non produite par ce type de
équipement, valeur non numérique...), chacune signalée séparément sans
bloquer les autres lignes.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

from adapters.json_equipment_catalog_adapter import JsonEquipmentCatalogAdapter
from adapters.nats_adapter import NatsJetStreamAdapter
from application.live_values_service import (
    load_values_file,
    parse_values,
    resolve_equipment_ids,
)
from infrastructure.secrets import read_secret
from infrastructure.zone_database import resolve_database_for_zone
from infrastructure.adapters.arcadedb_repository_adapter import ArcadeDbRepositoryAdapter
from list_equipment import fetch_zone_equipment
from application.live_simulation_service import DEFAULT_SUBJECT

DEFAULT_VALUES_PATH = os.path.join(os.path.dirname(__file__), "live", "valeurs.json")


def _unit_for(catalog: JsonEquipmentCatalogAdapter, equipment_type: str, metric_name: str) -> str:
    """Cherché à part plutôt que porté par ResolvedValue : garde
    live_values_service.py sans aucune dépendance au catalogue/à ArcadeDB
    (module pur, testable seul -- voir tests/test_live_values_service.py)."""
    try:
        spec = catalog.get_generator_spec(equipment_type)
    except KeyError:
        return ""
    for metric in spec.metrics:
        if metric.name == metric_name:
            return metric.unit
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fichier", default=DEFAULT_VALUES_PATH, help="Chemin du fichier de valeurs (défaut : live/valeurs.json)")
    parser.add_argument("--dry-run", action="store_true", help="Valider et afficher sans rien publier sur NATS")
    args = parser.parse_args()

    # --- Étape 1 : lire le fichier ---------------------------------------
    raw, error = load_values_file(args.fichier)
    if error:
        print(f"❌ {error}")
        sys.exit(1)

    # --- Étape 2 : valider les lignes -------------------------------------
    base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
    username = os.environ.get("ARCADEDB_USERNAME", "root")
    password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
    catalog = JsonEquipmentCatalogAdapter(
        os.path.join(os.path.dirname(__file__), "config", "equipment_simulation_catalog.json")
    )
    allowed_metrics = set(catalog.all_metric_names())

    parsed = parse_values(raw, allowed_metrics)
    for err in parsed.errors:
        print(f"⚠️  {err}")
    if not parsed.zone:
        print("❌ Fichier sans zone exploitable, rien à publier.")
        sys.exit(1)
    if not parsed.values:
        print("Aucune ligne valide à publier.")
        sys.exit(0)

    # --- Étape 3 : retrouver les global_id réels de la zone ---------------
    repo_factory = lambda db: ArcadeDbRepositoryAdapter(
        base_url=base_url, database=db, username=username, password=password,
    )
    database = resolve_database_for_zone(repo_factory, parsed.zone, for_write=False)
    if database is None:
        print(f"❌ Zone '{parsed.zone}' inconnue.")
        sys.exit(1)
    repo = repo_factory(database)

    equipment_by_name: dict[str, list[tuple[str, str]]] = {}
    equipment_types_present: set[str] = set()
    for name, global_id, equipment_type in fetch_zone_equipment(repo, parsed.zone):
        equipment_by_name.setdefault(name, []).append((global_id, equipment_type))
        equipment_types_present.add(equipment_type)

    metrics_by_type: dict[str, set[str]] = {}
    for equipment_type in equipment_types_present:
        try:
            spec = catalog.get_generator_spec(equipment_type)
        except KeyError:
            continue
        metrics_by_type[equipment_type] = {m.name for m in spec.metrics}

    resolved = resolve_equipment_ids(parsed.values, equipment_by_name, metrics_by_type)
    for err in resolved.errors:
        print(f"⚠️  {err}")
    if not resolved.resolved:
        print("Aucune valeur résolue, rien à publier.")
        sys.exit(0)

    # --- Étape 4 : construire les messages ---------------------------------
    now = datetime.now(timezone.utc)
    payloads = [
        {
            "global_id": r.global_id,
            "timestamp": now.isoformat(),
            "metric": r.metric,
            "value": r.value,
            "unit": _unit_for(catalog, r.equipment_type, r.metric),
            "quality": "good",
            "source": "file-replay",
        }
        for r in resolved.resolved
    ]

    print(f"\n{len(payloads)} valeur(s) prête(s) pour la zone '{parsed.zone}' :")
    for p in payloads:
        print(f"  {p['global_id']}  {p['metric']} = {p['value']} {p['unit']}")

    if args.dry_run:
        print("\n(--dry-run : rien publié)")
        return

    # --- Étape 5 : publier sur NATS -----------------------------------------
    nats_servers = os.environ.get("NATS_URL", "nats://localhost:4222")
    nats_user = os.environ.get("NATS_USER", "bos_admin")
    nats_password = read_secret("NATS_PASSWORD", "testBIM123")
    subject = os.environ.get("BOS_TIMESERIES_SUBJECT", DEFAULT_SUBJECT)

    adapter = NatsJetStreamAdapter(servers=nats_servers, user=nats_user, password=nats_password)
    adapter.publish_many(subject, payloads)
    print(f"\n✅ {len(payloads)} valeur(s) publiée(s) sur '{subject}'.")


if __name__ == "__main__":
    main()
