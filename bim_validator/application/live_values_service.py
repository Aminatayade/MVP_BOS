"""
Couche APPLICATION
--------------------
Ces fonctions permettent de vérifier si le fichier est lisible, ensuite de
vérifier si ses lignes sont valides, enfin de savoir ce qui a changé depuis
tout à l'heure -- utilisé par publish_values_file.py pour rejouer un
fichier de valeurs (live/valeurs.json) vers un équipement précis, sans
passer par le générateur aléatoire du catalogue.

Fonctions pures (aucun accès disque hors load_values_file, aucun accès
réseau) -- testables sans ArcadeDB ni NATS, même style que
energy_aggregation.py.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParsedValues:
    zone: str
    values: dict[tuple[str, str], float]
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ValuesDiff:
    changed: dict[tuple[str, str], float]
    removed: list[tuple[str, str]]
    unchanged: list[tuple[str, str]]


# Fonction 1 : lire le fichier -- ne lève jamais d'exception, renvoie
# toujours (données, erreur) avec l'un des deux à None.
def load_values_file(path: str) -> tuple[dict | None, str | None]:
    if not os.path.exists(path):
        return None, f"Fichier introuvable : {path}"
    try:
        # utf-8-sig : ignore le BOM qu'un éditeur Windows peut ajouter en
        # tête de fichier, sinon json.loads échoue sur un fichier valide.
        with open(path, "r", encoding="utf-8-sig") as f:
            content = f.read()
    except OSError as exc:
        return None, f"Lecture impossible : {exc}"
    if not content.strip():
        return None, "Fichier vide"
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        return None, f"JSON invalide : {exc}"
    if not isinstance(data, dict):
        return None, "Le fichier doit contenir un objet JSON {...}"
    return data, None


# Fonction 2 : valider les lignes -- une ligne invalide est signalée dans
# `errors` et ignorée, elle n'empêche jamais les autres lignes valides.
def parse_values(raw: dict, allowed_metrics: set[str]) -> ParsedValues:
    zone = raw.get("zone")
    if not isinstance(zone, str) or not zone.strip():
        return ParsedValues(zone="", values={}, errors=["Clé 'zone' absente ou vide"])
    lines = raw.get("valeurs")
    if not isinstance(lines, list):
        return ParsedValues(zone=zone.strip(), values={}, errors=["'valeurs' doit être une liste"])

    values: dict[tuple[str, str], float] = {}
    errors: list[str] = []
    for index, line in enumerate(lines, start=1):
        prefix = f"Ligne {index}"
        if not isinstance(line, dict):
            errors.append(f"{prefix} : doit être un objet")
            continue
        name = line.get("equipement")
        metric = line.get("metrique")
        value = line.get("valeur")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{prefix} : 'equipement' manquant")
            continue
        if not isinstance(metric, str) or metric not in allowed_metrics:
            errors.append(f"{prefix} : métrique inconnue ({metric!r})")
            continue
        # isinstance(value, bool) exclu explicitement : en Python, True/False
        # sont aussi des int (True == 1), donc "valeur": true serait sinon
        # accepté silencieusement comme 1.
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            errors.append(f"{prefix} : 'valeur' doit être un nombre")
            continue
        key = (name, metric)
        if key in values:
            errors.append(f"{prefix} : doublon ({name}, {metric})")
            continue
        values[key] = float(value)
    return ParsedValues(zone=zone.strip(), values=values, errors=errors)


# Fonction 3 : comparer l'ancienne et la nouvelle version -- dit quoi
# publier (changed), quoi rendre à la simulation (removed), et quoi laisser
# tel quel (unchanged).
def diff_values(
    old: dict[tuple[str, str], float], new: dict[tuple[str, str], float]
) -> ValuesDiff:
    changed = {key: value for key, value in new.items() if key not in old or old[key] != value}
    removed = [key for key in old if key not in new]
    unchanged = [key for key in new if key in old and old[key] == new[key]]
    return ValuesDiff(changed=changed, removed=removed, unchanged=unchanged)


@dataclass(frozen=True)
class ResolvedValue:
    global_id: str
    equipment_type: str
    metric: str
    value: float


@dataclass(frozen=True)
class ResolvedValues:
    resolved: list[ResolvedValue]
    errors: list[str]


# Fonction 4 : associer chaque (nom, métrique) validé à son global_id réel.
# Pure -- `equipment_by_name` est déjà construit par l'appelant à partir
# d'une requête sur le graphe (voir publish_values_file.py), jamais
# interrogé ici. Une liste (pas un seul global_id) par nom, pour pouvoir
# détecter un nom en double au lieu de n'en garder qu'un au hasard --
# exactement le piège rencontré en test manuel (deux robinets au nom
# presque identique, seul le numéro final diffère).
def resolve_equipment_ids(
    values: dict[tuple[str, str], float],
    equipment_by_name: dict[str, list[tuple[str, str]]],
    metrics_by_type: dict[str, set[str]],
) -> ResolvedValues:
    resolved: list[ResolvedValue] = []
    errors: list[str] = []
    for (name, metric), value in values.items():
        matches = equipment_by_name.get(name)
        if not matches:
            errors.append(f"'{name}' : équipement introuvable dans cette zone")
            continue
        if len(matches) > 1:
            errors.append(f"'{name}' : nom ambigu, {len(matches)} équipements portent ce nom")
            continue
        global_id, equipment_type = matches[0]
        allowed = metrics_by_type.get(equipment_type, set())
        if metric not in allowed:
            errors.append(
                f"'{name}' : le type '{equipment_type}' ne produit pas la métrique '{metric}'"
            )
            continue
        resolved.append(ResolvedValue(
            global_id=global_id, equipment_type=equipment_type, metric=metric, value=value,
        ))
    return ResolvedValues(resolved=resolved, errors=errors)
