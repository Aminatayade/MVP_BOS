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

from sqlalchemy import Column, String, ARRAY, DateTime, func
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