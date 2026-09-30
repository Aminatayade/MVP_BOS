"""
Script utilitaire : affiche les équipements d'une zone, avec leur nom exact
et les métriques qu'ils produisent -- sert de "carte" pour remplir
live/valeurs.json sans deviner un nom ni confondre deux équipements
presque identiques (cas réel rencontré : deux robinets ne différant que par
leur numéro final, tronqués de façon identique dans la pop-up 3D).

N'écrit jamais dans valeurs.json : c'est un outil de consultation, pas de
génération -- le fichier de démonstration reste volontairement court (quelques
lignes), pas une liste exhaustive des 500+ équipements d'un bâtiment comme
Batipompe.

Usage :
    python list_equipment.py Batipompe
    python list_equipment.py Batipompe --type vanne_arret
    python list_equipment.py Batipompe --contient "robinet"
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import requests

from adapters.json_equipment_catalog_adapter import JsonEquipmentCatalogAdapter
from infrastructure.secrets import read_secret
from infrastructure.zone_database import resolve_database_for_zone
from infrastructure.adapters.arcadedb_repository_adapter import ArcadeDbRepositoryAdapter


def _cypher_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def fetch_zone_equipment(repo, zone: str) -> list[tuple[str, str, str]]:
    """(nom, global_id, equipment_type) de TOUS les équipements d'une zone
    -- même filtre `WHERE zone = ...` que _fetch_zone_equipment dans
    timeseries_api.py (zone est une propriété directe du nœud Equipment,
    pas besoin de traverser le graphe)."""
    escaped_zone = _cypher_escape(zone)
    response = repo.query_sql(
        "SELECT name, global_id, equipment_type FROM Equipment "
        f"WHERE zone = '{escaped_zone}' AND equipment_type IS NOT NULL"
    )
    rows = response.get("result", [])
    return [
        (row["name"], row["global_id"], row["equipment_type"])
        for row in rows
        if row.get("name") and row.get("global_id") and row.get("equipment_type")
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("zone", help="Nom exact de la zone (ex: Batipompe)")
    parser.add_argument("--type", help="Ne montrer qu'un equipment_type précis (ex: vanne_arret)")
    parser.add_argument("--contient", help="Ne montrer que les noms contenant ce texte (insensible à la casse)")
    args = parser.parse_args()

    base_url = os.environ.get("ARCADEDB_BASE_URL", "http://localhost:2480")
    username = os.environ.get("ARCADEDB_USERNAME", "root")
    password = read_secret("ARCADEDB_PASSWORD", "testBIM123")
    repo_factory = lambda db: ArcadeDbRepositoryAdapter(
        base_url=base_url, database=db, username=username, password=password,
    )

    database = resolve_database_for_zone(repo_factory, args.zone, for_write=False)
    if database is None:
        print(f"❌ Zone '{args.zone}' inconnue.", file=sys.stderr)
        sys.exit(1)
    repo = repo_factory(database)

    catalog = JsonEquipmentCatalogAdapter(
        os.path.join(os.path.dirname(__file__), "config", "equipment_simulation_catalog.json")
    )

    equipment = fetch_zone_equipment(repo, args.zone)
    if args.type:
        equipment = [e for e in equipment if e[2] == args.type]
    if args.contient:
        needle = args.contient.lower()
        equipment = [e for e in equipment if needle in e[0].lower()]

    if not equipment:
        print("Aucun équipement ne correspond à ces filtres.")
        return

    print(f"{len(equipment)} équipement(s) dans la zone '{args.zone}' :\n")
    for name, global_id, equipment_type in sorted(equipment, key=lambda e: (e[2], e[0])):
        try:
            spec = catalog.get_generator_spec(equipment_type)
            metrics = ", ".join(m.name for m in spec.metrics) or "(aucune métrique simulée)"
        except KeyError:
            metrics = "(type absent du catalogue)"
        print(f"[{equipment_type}] {name}")
        print(f"    métriques : {metrics}")
        print(f"    global_id : {global_id}\n")


if __name__ == "__main__":
    main()
