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

from secrets_util import read_secret

# -----------------------------------------------------------------------------
# CHAÎNE DE CONNEXION À LA BDD MÉTIER
# DATABASE_URL reste une échappatoire explicite (connexion non standard), mais
# n'est plus la voie normale : embarquer le mot de passe DANS l'URL le rend
# visible en clair via `docker inspect` sur la variable d'environnement
# entière. Par défaut, l'URL est reconstruite à partir de ses parties, avec
# le mot de passe lu via read_secret() (fichier de secret Docker en
# priorité, voir secrets_util.py) -- inchangé pour qui ne configure pas de
# secret Docker.
# -----------------------------------------------------------------------------
_explicit_database_url = os.getenv("DATABASE_URL")
if _explicit_database_url:
    SQLALCHEMY_DATABASE_URL = _explicit_database_url
else:
    _pg_user = os.getenv("POSTGRES_APP_USER", "bos_app_user")
    _pg_password = read_secret("POSTGRES_APP_PASSWORD", "bos_app_password")
    _pg_host = os.getenv("POSTGRES_APP_HOST", "postgres-app")
    _pg_port = os.getenv("POSTGRES_APP_PORT", "5432")
    _pg_db = os.getenv("POSTGRES_APP_DB", "bos_app_db")
    SQLALCHEMY_DATABASE_URL = f"postgresql://{_pg_user}:{_pg_password}@{_pg_host}:{_pg_port}/{_pg_db}"

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