"""
FILE: database.py
DESCRIPTION: Gestion du moteur de connexion SQLAlchemy et de la création des sessions 
             pour la base de données métier PostgreSQL applicative (bos_postgres_app).

INPUTS:
  - Variable d'environnement DATABASE_URL (ou chaîne par défaut de connexion Docker interne).
  - Identifiants applicatifs BDD : bos_app_user / bos_app_password.
  - Hôte réseau inter-conteneurs : 'postgres-app' sur le port interne 5432.

OUTPUTS:
  - Moteur SQLAlchemy 'engine' instancié.
  - Classe d'usine 'SessionLocal' pour la gestion des sessions ORM.
  - Classe de base 'Base' pour le mapping déclaratif des modèles métiers.
  - Générateur 'get_db' injectant des sessions BDD isolées par requête HTTP (FastAPI Dependency).

DEPENDENCIES:
  - os (lecture des variables d'environnement)
  - sqlalchemy (create_engine, declarative_base, sessionmaker)

NOTES / PRÉREQUIS:
  - En environnement conteneurisé (Docker), l'hôte doit correspondre au NOM DU SERVICE
    défini dans docker-compose.yml ('postgres-app') et au port interne '5432'.
  - Le fallback 'localhost:5433' reste accessible si le script est exécuté hors Docker.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# -----------------------------------------------------------------------------
# CHAÎNE DE CONNEXION À LA BDD MÉTIER
# Priorité à la variable d'environnement Docker. Fallback sur le réseau Docker inter-conteneurs.
# -----------------------------------------------------------------------------
SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://bos_app_user:bos_app_password@postgres-app:5432/bos_app_db"
)

# -----------------------------------------------------------------------------
# INITIALISATION DE SQLALCHEMY
# -----------------------------------------------------------------------------
# Création du moteur de connexion SQLAlchemy relié à PostgreSQL
engine = create_engine(SQLALCHEMY_DATABASE_URL)

# Usine à sessions : génère des sessions BDD isolées avec commit/flush explicites
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base déclarative héritée par l'ensemble des modèles ORM de l'application
Base = declarative_base()

# -----------------------------------------------------------------------------
# DÉPENDANCE FASTAPI (INJECTION DE SESSiON)
# -----------------------------------------------------------------------------
def get_db():
    """
    Générateur (yield) servant de dépendance FastAPI pour fournir une session BDD.
    Garantit la fermeture propre de la connexion à la fin de chaque requête HTTP.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()