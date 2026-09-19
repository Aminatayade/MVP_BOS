"""
FILE: models.py
DESCRIPTION: Modèle Objet-Relationnel (ORM) SQLAlchemy définissant la structure de la table 'phases'
             stockant les informations métier des cartouches et flèches du workflow (Phases A, B, C).

INPUTS:
  - Base déclarative de SQLAlchemy (database.Base)

OUTPUTS:
  - Classe ORM 'PhaseModel' cartographiée sur la table PostgreSQL 'phases'

DEPENDENCIES:
  - sqlalchemy

NOTES / PRÉREQUIS:
  - Utilisé par database.py et main.py pour la création et la requête de la structure des données métier.
"""

import uuid

from sqlalchemy import Column, String, ARRAY, DateTime, Numeric, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
# Remplacer "from .database import Base" par :
from database import Base


class CockpitModel(Base):
    """Composition de cockpit sauvegardée (bibliothèque BOS Exécution) --
    voir web-app-execution/frontend-client/.../features/cockpits/ pour le
    schéma JSON exact de `composition`. PK en hex UUID (pas de convention
    UUID/int déjà établie dans ce fichier -- id="A"/"B"/"C" pour PhaseModel
    n'a de sens que pour des phases fixes en petit nombre, pas ici)."""
    __tablename__ = "cockpits"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    owner_username = Column(String, nullable=False, index=True)  # preferred_username Keycloak (voir security.get_current_user)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    category = Column(String, nullable=True)  # texte libre (Énergie/Occupation/Confort/...), jamais un Enum figé -- extensible sans migration
    tags = Column(ARRAY(String), default=list)
    composition = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MaintenanceSlaReferenceModel(Base):
    """Référence contractuelle SLA par (métier, sous_categorie) -- le
    prestataire en découle (un seul par couple, voir contrainte d'unicité),
    plusieurs tickets partagent une même ligne. Le ticket copie `sla_hours`
    à l'ouverture (voir MaintenanceTicketModel) pour que son ratio reste
    stable même si cette référence est éditée plus tard."""
    __tablename__ = "maintenance_sla_references"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    metier = Column(String, nullable=False, index=True)
    sous_categorie = Column(String, nullable=False)
    prestataire = Column(String, nullable=False)
    sla_hours = Column(Numeric, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("metier", "sous_categorie", name="uq_maintenance_sla_metier_sous_categorie"),)


class MaintenanceTicketModel(Base):
    """Ticket d'intervention de maintenance (cockpit BOS Exécution). Pas de
    FK vers ArcadeDB (zone/étage en texte libre, voir zone_database côté
    bim_validator pour le graphe -- deux bases séparées, aucun lien possible)."""
    __tablename__ = "maintenance_tickets"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    equipement = Column(String, nullable=False)
    metier = Column(String, nullable=False, index=True)
    sous_categorie = Column(String, nullable=False, index=True)
    prestataire = Column(String, nullable=False)
    zone = Column(String, nullable=False, index=True)
    etage = Column(String, nullable=False, index=True)
    statut_workflow = Column(String, nullable=False, index=True)  # "en_cours" | "traite"
    priorite = Column(String, nullable=False, index=True)  # "elevee" | "moyenne" | "basse"
    date_ouverture = Column(DateTime(timezone=True), nullable=False)
    date_cloture = Column(DateTime(timezone=True), nullable=True)
    sla_hours = Column(Numeric, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class MaintenanceSettingsModel(Base):
    """Table singleton (une seule ligne, id="default") -- même logique que
    PhaseModel (id="A"/"B"/"C" fixes) pour un petit nombre de lignes connu
    à l'avance, ici réduit à une seule."""
    __tablename__ = "maintenance_settings"

    id = Column(String, primary_key=True, default="default")
    seuil_alerte = Column(Numeric, nullable=False, default=15)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OccupancySettingsModel(Base):
    """Table singleton (une seule ligne, id="default") -- DEUX seuils
    d'occupation (bas/haut) partagés par le cockpit "Compréhension du taux
    d'usage des espaces" (widgets seuils/compteurs, voir
    bim_validator/api_server.py /graph/occupancy-dashboard qui les reçoit en
    paramètres). Deux bornes, pas une seule valeur : "occupation < seuil_bas"
    signale un espace SOUS-utilisé, "occupation > seuil_haut" un espace
    SATURÉ -- l'entre-deux n'est signalé nulle part (confirmé avec le client,
    ex. 20%/80%). Config relationnelle simple : reste côté Postgres/
    api-gateway comme MaintenanceSettingsModel, pas dans ArcadeDB qui n'a
    aucun précédent de type "réglage"."""
    __tablename__ = "occupancy_settings"

    id = Column(String, primary_key=True, default="default")
    seuil_bas = Column(Numeric, nullable=False, default=20)
    seuil_haut = Column(Numeric, nullable=False, default=80)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PhaseModel(Base):
    __tablename__ = "phases"

    # Identifiant unique de la phase (ex: "A", "B", "C")
    id = Column(String, primary_key=True, index=True)
    nom = Column(String, nullable=False)               # Ex: "Phase A"
    titre = Column(String, nullable=False)             # Ex: "Initialisation & Analyse"
    description = Column(String, nullable=False)      # Texte de description dans la cartouche
    badge_color = Column(String, default="#6366f1")    # Code couleur CSS du badge
    
    # Éléments structurés du workflow
    cartouche_points = Column(ARRAY(String))           # Liste de puces à l'intérieur de la cartouche
    etape_nom = Column(String, nullable=False)         # Intitulé inscrit dans la flèche de workflow
    livrables = Column(String, nullable=False)        # Texte des livrables associés au bas de la flèche