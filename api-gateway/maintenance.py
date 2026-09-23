"""
FILE: maintenance.py
DESCRIPTION: Cockpit maintenance "Statistiques d'interventions" (BOS
             Exécution) -- filtres en cascade métier/sous-catégorie/
             prestataire, agrégats pour 7 widgets, et seuil d'alerte
             configurable partagé entre les widgets "par zone" et "seuils
             dépassés". Périmètre volontairement limité au tableau de bord +
             un jeu de données de démonstration : pas de route de création/
             édition de ticket dans cette itération (reporté).

Le cockpit est scopé par ZONE, comme le Jumeau numérique et le Cockpit
Énergie (même liste de zones, voir bim_proxy.py / GET /api/bim/graph/zones,
appelée côté frontend via features/bim/services/arcadeApi.ts:getZones()) --
chaque projet/bâtiment a ses propres tickets, jamais un jeu de données
générique partagé entre projets.

INPUTS:
  - Table Postgres `maintenance_tickets` / `maintenance_sla_references` /
    `maintenance_settings` (bos_app_db, voir database.py). Les références SLA
    et le seuil d'alerte sont globaux (config produit, pas par projet) ;
    les tickets sont scopés par `zone` et alimentés par un seed paresseux PAR
    ZONE (_seed_tickets_if_empty) au premier appel pour cette zone, comme
    /api/v1/phases dans main.py mais répété une fois par zone jamais vue.

OUTPUTS:
  - Router FastAPI monté sous /api/v1/maintenance (voir main.py).

DEPENDENCIES:
  - sqlalchemy, fastapi
  - security.verify_execution_allowed_roles (même RBAC que /api/v1/cockpits :
    Administrateur/Configurateur/Client ont tous accès lecture+écriture ici)
"""

import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from models import MaintenanceSettingsModel, MaintenanceSlaReferenceModel, MaintenanceTicketModel
from schemas import MaintenanceSettingsIn
from security import verify_execution_allowed_roles

router = APIRouter(prefix="/api/v1/maintenance", tags=["maintenance"])

# Même variable d'env que bim_proxy.py/widget_aggregate.py -- pour que les
# tickets de démo utilisent les VRAIS étages de la maquette IFC ingérée
# (ex: "MTAL_5_NF_RDC" pour Batipompe), pas une liste générique "RDC/R+1/...".
BIM_API_INTERNAL_URL = os.getenv("BIM_API_INTERNAL_URL", "http://bim_api:8000").rstrip("/")

SETTINGS_SINGLETON_ID = "default"
DEFAULT_SEUIL_ALERTE = 15

# (métier, sous_categorie, prestataire, sla_hours) -- un seul prestataire par
# couple métier/sous-catégorie (voir contrainte d'unicité du modèle), c'est
# ce qui permet au badge du filtre en cascade d'être en lecture seule. Config
# GLOBALE (pas par zone) : ce référentiel contractuel est le même quel que
# soit le bâtiment.
_SLA_REFERENCE_SEED = [
    ("MCR", "Sondes thermiques", "Société Y", 24),
    ("MCR", "Automates", "Société Z", 48),
    ("Électricité", "Éclairage", "Société A", 8),
    ("Électricité", "Tableaux électriques", "Société B", 12),
    ("CVC", "Climatisation", "Société C", 24),
    ("CVC", "Ventilation", "Société D", 24),
    ("Sanitaire", "Plomberie", "Société E", 12),
    ("Sanitaire", "Sanitaires", "Société F", 6),
]

# Poids relatifs par métier (somme non normalisée, cf. random.choices).
_METIER_WEIGHTS = {"MCR": 28, "Électricité": 28, "CVC": 24, "Sanitaire": 20}

# Repli si la zone n'a pas encore de maquette IFC ingérée (aucun Floor dans
# ArcadeDB) -- le cockpit doit rester démontrable même pour un projet créé
# côté BOS mais pas encore modélisé en 3D.
_FALLBACK_ETAGES = ["RDC", "R+1", "R+2", "R+3", "R+4"]

_PRIORITE_WEIGHTS = [("elevee", 20), ("moyenne", 50), ("basse", 30)]

TICKET_COUNT_PER_ZONE = 142

# Bornes des tranches de durée de traitement, en heures.
_DURATION_BUCKETS = [
    ("<4h", 0, 4),
    ("4-24h", 4, 24),
    ("1-3j", 24, 72),
    (">3j", 72, None),
]

# Fenêtre de temps couverte par chaque période -- pas de flux temps réel dans
# ce MVP (données de démo statiques) : "temps_reel" = dernières 6h, complétées
# par _top_up_recent_tickets pour que ces périodes courtes ne soient pas vides.
_PERIODE_WINDOW = {
    "temps_reel": timedelta(hours=6),
    "jour": timedelta(days=1),
    "semaine": timedelta(days=7),
    "mois": timedelta(days=30),
    "trimestre": timedelta(days=90),
    "annee": timedelta(days=365),
}


def _seed_global_config_if_empty(db: Session) -> None:
    """Référentiel SLA + seuil d'alerte : config produit globale, indépendante
    de la zone -- seedée une seule fois, jamais par projet."""
    if db.query(MaintenanceSlaReferenceModel).count() == 0:
        db.add_all(
            MaintenanceSlaReferenceModel(
                metier=metier, sous_categorie=sous_categorie, prestataire=prestataire, sla_hours=sla_hours
            )
            for metier, sous_categorie, prestataire, sla_hours in _SLA_REFERENCE_SEED
        )
        db.commit()

    if db.query(MaintenanceSettingsModel).count() == 0:
        db.add(MaintenanceSettingsModel(id=SETTINGS_SINGLETON_ID, seuil_alerte=DEFAULT_SEUIL_ALERTE))
        db.commit()


def _fetch_real_etages(zone: str) -> list[str]:
    """Étages RÉELS de la maquette IFC ingérée pour cette zone (Floor nodes
    ArcadeDB, ex: "MTAL_5_NF_RDC" pour Batipompe) -- voir bim_proxy.py
    GET /graph/floors. Retourne [] si la zone n'a pas encore de maquette
    ingérée ou si bim_api est injoignable (le seed retombe alors sur
    _FALLBACK_ETAGES) : jamais d'exception qui casserait le dashboard."""
    try:
        resp = httpx.get(f"{BIM_API_INTERNAL_URL}/graph/floors", params={"zone": zone}, timeout=10.0)
        resp.raise_for_status()
        rows = resp.json().get("result", {}).get("result", [])
        names = []
        seen = set()
        for row in rows:
            floor = row.get("f", row)
            name = floor.get("name") or floor.get("global_id")
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        return names
    except Exception:
        return []


def _build_tickets(db: Session, zone: str, rng: random.Random, dates: list[datetime]) -> list[MaintenanceTicketModel]:
    references = db.query(MaintenanceSlaReferenceModel).all()
    by_metier: dict[str, list[MaintenanceSlaReferenceModel]] = {}
    for ref in references:
        by_metier.setdefault(ref.metier, []).append(ref)

    metiers = list(_METIER_WEIGHTS.keys())
    metier_weights = list(_METIER_WEIGHTS.values())
    etages = _fetch_real_etages(zone) or list(_FALLBACK_ETAGES)
    # Poids par étage aléatoires MAIS déterministes par zone (nombre/noms des
    # étages inconnus à l'avance) : contraste entre étages, pas d'uniformité.
    etage_rng = random.Random(f"maintenance-demo-etages-{zone}")
    etage_weights = [etage_rng.uniform(1, 3) for _ in etages]
    priorites = [p for p, _ in _PRIORITE_WEIGHTS]
    priorite_weights = [w for _, w in _PRIORITE_WEIGHTS]

    now = datetime.now(timezone.utc)
    tickets = []
    for date_ouverture in dates:
        metier = rng.choices(metiers, weights=metier_weights, k=1)[0]
        ref = rng.choice(by_metier[metier])
        etage = rng.choices(etages, weights=etage_weights, k=1)[0]
        priorite = rng.choices(priorites, weights=priorite_weights, k=1)[0]
        statut_workflow = "traite" if rng.random() < 0.75 else "en_cours"

        date_cloture = None
        if statut_workflow == "traite":
            duree_reelle_h = float(ref.sla_hours) * rng.uniform(0.5, 1.8)
            date_cloture = date_ouverture + timedelta(hours=duree_reelle_h)
            if date_cloture > now:
                # Un ticket ne peut pas être clôturé dans le futur : encore en cours.
                statut_workflow, date_cloture = "en_cours", None

        tickets.append(
            MaintenanceTicketModel(
                id=uuid.uuid4().hex,
                equipement=f"{ref.sous_categorie} — {etage}",
                metier=metier,
                sous_categorie=ref.sous_categorie,
                prestataire=ref.prestataire,
                zone=zone,
                etage=etage,
                statut_workflow=statut_workflow,
                priorite=priorite,
                date_ouverture=date_ouverture,
                date_cloture=date_cloture,
                sla_hours=ref.sla_hours,
            )
        )
    return tickets


def _seed_tickets_if_empty(db: Session, zone: str) -> None:
    """Jeu de tickets de démonstration PAR ZONE : la première fois qu'une
    zone (un projet BIM déjà ingéré ou non) est consultée dans ce cockpit,
    elle reçoit son propre lot de tickets -- c'est ce qui permet au module de
    s'adapter automatiquement à n'importe quel projet sans redéploiement.

    Ensuite, à chaque consultation, on garantit des tickets RÉCENTS (voir
    _top_up_recent_tickets) pour que les périodes courtes ne soient pas vides."""
    if db.query(MaintenanceTicketModel).filter(MaintenanceTicketModel.zone == zone).count() == 0:
        rng = random.Random(f"maintenance-demo-{zone}")
        now = datetime.now(timezone.utc)
        dates = [now - timedelta(days=rng.uniform(0, 180)) for _ in range(TICKET_COUNT_PER_ZONE)]
        db.add_all(_build_tickets(db, zone, rng, dates))
        db.commit()
    _top_up_recent_tickets(db, zone)


# (fenêtre en heures, nombre minimal de tickets ouverts dans cette fenêtre)
_RECENT_MINIMUMS = [(6, 3), (24, 8), (24 * 7, 25)]


def _top_up_recent_tickets(db: Session, zone: str) -> None:
    """Démo statique : les dates du seed vieillissent, donc "Temps réel",
    "Jour" et "Semaine" finiraient vides. Complète, si besoin, jusqu'à un
    minimum de tickets récents par fenêtre (idempotent : rien à ajouter tant
    que les minimums sont atteints)."""
    now = datetime.now(timezone.utc)
    rng = random.Random(f"maintenance-recent-{zone}-{now.isoformat()}")
    dates: list[datetime] = []
    for hours, minimum in _RECENT_MINIMUMS:
        count = (
            db.query(MaintenanceTicketModel)
            .filter(MaintenanceTicketModel.zone == zone, MaintenanceTicketModel.date_ouverture >= now - timedelta(hours=hours))
            .count()
        ) + len(dates)
        # Les dates déjà ajoutées pour une fenêtre plus courte comptent dans les plus larges.
        for _ in range(max(0, minimum - count)):
            dates.append(now - timedelta(hours=rng.uniform(0.1, hours)))
    if dates:
        db.add_all(_build_tickets(db, zone, rng, dates))
        db.commit()


def _get_or_create_settings(db: Session) -> MaintenanceSettingsModel:
    settings = db.query(MaintenanceSettingsModel).filter(MaintenanceSettingsModel.id == SETTINGS_SINGLETON_ID).first()
    if not settings:
        settings = MaintenanceSettingsModel(id=SETTINGS_SINGLETON_ID, seuil_alerte=DEFAULT_SEUIL_ALERTE)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def _apply_threshold(counts: dict[str, int], seuil: float) -> list[dict]:
    """Point d'entrée UNIQUE comparant un compte au seuil -- appelé une fois
    pour les zones/étages et une fois pour les métiers (voir /dashboard), afin
    que le widget "par zone" (colonnes en dépassement) et le widget "seuils
    dépassés" ne puissent jamais diverger sur ce qui dépasse ou non."""
    breaches = []
    for label, count in counts.items():
        if count <= seuil:
            continue
        severity = "warning" if count <= seuil * 1.5 else "danger"
        breaches.append({"label": label, "count": count, "threshold": seuil, "severity": severity})
    breaches.sort(key=lambda b: b["count"], reverse=True)
    return breaches


@router.get("/filters")
def get_filters(
    zone: str = Query(..., description="Zone/projet BOS (même liste que Jumeau numérique / Cockpit Énergie)"),
    db: Session = Depends(get_db),
    roles: list = Depends(verify_execution_allowed_roles),
):
    _seed_global_config_if_empty(db)
    _seed_tickets_if_empty(db, zone)

    references = db.query(MaintenanceSlaReferenceModel).order_by(MaintenanceSlaReferenceModel.metier).all()
    metiers: dict[str, list[dict]] = {}
    for ref in references:
        metiers.setdefault(ref.metier, []).append({"sous_categorie": ref.sous_categorie, "prestataire": ref.prestataire})

    etages = [
        row[0]
        for row in db.query(MaintenanceTicketModel.etage)
        .filter(MaintenanceTicketModel.zone == zone)
        .distinct()
        .order_by(MaintenanceTicketModel.etage)
        .all()
    ]

    return {
        "metiers": [{"metier": metier, "sous_categories": sous_categories} for metier, sous_categories in metiers.items()],
        "etages": etages,
    }


def _filtered_tickets_query(
    db: Session,
    *,
    zone: str,
    metier: Optional[str],
    sous_categorie: Optional[str],
    statut_workflow: Optional[str],
    priorite: Optional[str],
    etage: Optional[str],
    periode: str,
):
    query = db.query(MaintenanceTicketModel).filter(MaintenanceTicketModel.zone == zone)
    if metier:
        query = query.filter(MaintenanceTicketModel.metier == metier)
    if sous_categorie:
        query = query.filter(MaintenanceTicketModel.sous_categorie == sous_categorie)
    if statut_workflow:
        query = query.filter(MaintenanceTicketModel.statut_workflow == statut_workflow)
    if priorite:
        query = query.filter(MaintenanceTicketModel.priorite == priorite)
    if etage:
        query = query.filter(MaintenanceTicketModel.etage == etage)

    window = _PERIODE_WINDOW.get(periode, _PERIODE_WINDOW["mois"])
    cutoff = datetime.now(timezone.utc) - window
    query = query.filter(MaintenanceTicketModel.date_ouverture >= cutoff)
    return query


@router.get("/dashboard")
def get_dashboard(
    zone: str = Query(..., description="Zone/projet BOS (même liste que Jumeau numérique / Cockpit Énergie)"),
    metier: Optional[str] = Query(None),
    sous_categorie: Optional[str] = Query(None),
    statut_workflow: Optional[str] = Query(None),
    priorite: Optional[str] = Query(None),
    etage: Optional[str] = Query(None),
    periode: str = Query("mois"),
    db: Session = Depends(get_db),
    roles: list = Depends(verify_execution_allowed_roles),
):
    _seed_global_config_if_empty(db)
    _seed_tickets_if_empty(db, zone)

    tickets = _filtered_tickets_query(
        db, zone=zone, metier=metier, sous_categorie=sous_categorie, statut_workflow=statut_workflow,
        priorite=priorite, etage=etage, periode=periode,
    ).all()

    settings = _get_or_create_settings(db)
    seuil = float(settings.seuil_alerte)

    # Bucket temporel : "jour" pour les périodes courtes/moyennes (une vue
    # "Mois" reste lisible avec des barres par jour), "mois" seulement pour
    # les périodes longues où un point par jour serait illisible.
    bucket = "mois" if periode in ("trimestre", "annee") else "jour"
    time_counts: dict[str, int] = {}
    for ticket in tickets:
        label = ticket.date_ouverture.strftime("%Y-%m") if bucket == "mois" else ticket.date_ouverture.strftime("%Y-%m-%d")
        time_counts[label] = time_counts.get(label, 0) + 1
    time_series_points = [{"label": label, "count": count} for label, count in sorted(time_counts.items())]

    by_metier_counts: dict[str, int] = {}
    by_etage_counts: dict[str, int] = {}
    duration_bucket_counts = {label: 0 for label, _, _ in _DURATION_BUCKETS}

    closed_ratios = []
    for ticket in tickets:
        by_metier_counts[ticket.metier] = by_metier_counts.get(ticket.metier, 0) + 1
        by_etage_counts[ticket.etage] = by_etage_counts.get(ticket.etage, 0) + 1

        if ticket.date_cloture is None:
            continue
        duree_h = (ticket.date_cloture - ticket.date_ouverture).total_seconds() / 3600
        for label, lower, upper in _DURATION_BUCKETS:
            if duree_h >= lower and (upper is None or duree_h < upper):
                duration_bucket_counts[label] += 1
                break
        closed_ratios.append(duree_h / float(ticket.sla_hours))

    kpi_sla_ratio = round(sum(closed_ratios) / len(closed_ratios), 2) if closed_ratios else None

    etage_breaches = _apply_threshold(by_etage_counts, seuil)
    metier_breaches = _apply_threshold(by_metier_counts, seuil)
    alerts = (
        [{"type": "zone", **breach} for breach in etage_breaches]
        + [{"type": "metier", **breach} for breach in metier_breaches]
    )
    alerts.sort(key=lambda a: a["count"], reverse=True)
    etage_breach_labels = {b["label"] for b in etage_breaches}

    return {
        "kpi_total_tickets": len(tickets),
        "kpi_sla_ratio": kpi_sla_ratio,
        "time_series": {"bucket": bucket, "points": time_series_points},
        "by_metier": [{"metier": metier, "count": count} for metier, count in sorted(by_metier_counts.items(), key=lambda kv: -kv[1])],
        "by_duration_bucket": [{"bucket": label, "count": duration_bucket_counts[label]} for label, _, _ in _DURATION_BUCKETS],
        "by_zone": [
            {"zone": etage, "count": count, "over_threshold": etage in etage_breach_labels}
            for etage, count in sorted(by_etage_counts.items())
        ],
        "alerts": alerts,
        "seuil_alerte": seuil,
    }


@router.get("/settings")
def get_settings(db: Session = Depends(get_db), roles: list = Depends(verify_execution_allowed_roles)):
    settings = _get_or_create_settings(db)
    return {"seuil_alerte": float(settings.seuil_alerte)}


@router.put("/settings")
def update_settings(
    body: MaintenanceSettingsIn,
    db: Session = Depends(get_db),
    roles: list = Depends(verify_execution_allowed_roles),
):
    settings = _get_or_create_settings(db)
    settings.seuil_alerte = body.seuil_alerte
    db.commit()
    db.refresh(settings)
    return {"seuil_alerte": float(settings.seuil_alerte)}
