"""
Point d'entrée. Accepte PLUSIEURS maquettes en argument, chacune tagguée
avec sa discipline (ARCHI, CVC, PLOMBERIE, ELEC...), et applique à chacune
la charte de SA discipline.

Usage :
    python main.py archi:maquettes/batiment.ifc cvc:maquettes/ventilation.ifc plomberie:maquettes/sanitaire.ifc

Chaque argument doit être au format "discipline:chemin_du_fichier".
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from adapters.ifcopenshell_adapter import IfcOpenShellAdapter
from adapters.json_rules_adapter import JsonRulesRepositoryAdapter
from adapters.seaweedfs_adapter import SeaweedFsStorageAdapter
from application.bim_validator_service import BimValidatorService
from application.bim_ingestion_service import BimIngestionService


def parse_arguments(argv: list[str]) -> list[tuple[str, str]]:
    """Transforme ["archi:a.ifc", "cvc:b.ifc"] en [("archi","a.ifc"), ("cvc","b.ifc")]."""
    parsed = []
    for arg in argv:
        if ":" not in arg:
            print(f"  Argument ignoré (format attendu 'discipline:chemin') : {arg}")
            continue
        discipline, _, path = arg.partition(":")
        parsed.append((discipline.strip(), path.strip()))
    return parsed


def main():
    config_path = os.path.join(os.path.dirname(__file__), "config", "charte_bim_rules.json")
    seaweedfs_master_url = os.environ.get("SEAWEEDFS_MASTER_URL", "http://localhost:9333")

    reader = IfcOpenShellAdapter()
    rules_repo = JsonRulesRepositoryAdapter(config_path)
    storage = SeaweedFsStorageAdapter(master_url=seaweedfs_master_url)

    validator_service = BimValidatorService(reader, rules_repo)
    ingestion_service = BimIngestionService(validator_service, storage)

    args = sys.argv[1:]
    if not args:
        print("Usage : python main.py discipline1:fichier1.ifc discipline2:fichier2.ifc ...")
        print(f"Disciplines disponibles : {rules_repo.list_disciplines()}")
        sys.exit(1)

    jobs = parse_arguments(args)
    if not jobs:
        print("Aucun argument valide fourni.")
        sys.exit(1)

    results = []
    for discipline, file_path in jobs:
        print(f"\n [{discipline.upper()}] Analyse de {file_path}...")
        # On instancie un reader neuf par fichier : IfcOpenShellAdapter garde
        # un état interne (le modèle chargé), donc pas de réutilisation entre
        # deux maquettes différentes dans la même boucle.
        validator_service = BimValidatorService(IfcOpenShellAdapter(), rules_repo)
        ingestion_service = BimIngestionService(validator_service, storage)

        result = ingestion_service.ingest(file_path, discipline)
        results.append((discipline, file_path, result))

        print(f"RAPPORT [{discipline.upper()}] {file_path} :")
        print(result.to_dict())
        print("\n VERSION TEXTE POUR TECHNICIEN :")
        print(result.validation.to_human_text())

    # --- Résumé global, utile quand le client uploade tout un lot d'un coup ---
    print("\n" + "=" * 60)
    print("RÉSUMÉ DU LOT")
    print("=" * 60)
    for discipline, file_path, result in results:
        status = " CONFORME" if result.validation.success else "REJETÉE"
        stored = "stockée" if result.stored else "non stockée"
        print(f"[{discipline.upper():12}] {file_path:40} {status} ({stored})")


if __name__ == "__main__":
    main()
