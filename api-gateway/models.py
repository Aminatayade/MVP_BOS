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

from sqlalchemy import Column, String, ARRAY
# Remplacer "from .database import Base" par :
from database import Base

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