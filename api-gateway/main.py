"""
FILE: main.py
DESCRIPTION: Serveur principal FastAPI servant d'API Gateway. Il gère l'authentification Keycloak, 
             le contrôle d'accès strict par rôles (RBAC), autorise le CORS et sert les routes métier 
             (Phases/Cartouches) connectées à la BDD PostgreSQL applicative.

INPUTS:
  - Token JWT (Header HTTP Authorization: Bearer <token>)
  - Variables d'environnement Keycloak (KEYCLOAK_INTERNAL_URL / KEYCLOAK_EXTERNAL_URL)
  - Requêtes HTTP provenant de l'application Web React (http://localhost:5173)
  - Connexion BDD Métier (Session SQLAlchemy issue de database.py)

OUTPUTS:
  - Réponses JSON (validation d'accès, données des phases/cartouches métier, erreurs HTTP 401/403)
  - Documentation Swagger interactive générée sur /docs

DEPENDENCIES:
  - fastapi, uvicorn, python-jose, requests, sqlalchemy

NOTES / PRÉREQUIS:
  - En environnement conteneurisé (Docker) :
    * KEYCLOAK_INTERNAL_URL pointe vers le réseau Docker interne ('http://keycloak:8080') 
      afin d'interroger la clé publique JWKS depuis FastAPI.
    * KEYCLOAK_EXTERNAL_URL pointe vers l'adresse vue du navigateur ('http://localhost:8080')
      utilisée pour la génération du Swagger OAuth2.
"""

import os
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from jose import jwt
import requests
from sqlalchemy.orm import Session

# Imports des modules BDD et modèles locaux
from database import engine, get_db, Base
from models import PhaseModel

# Génération / Vérification de la présence des tables en BDD au démarrage
Base.metadata.create_all(bind=engine)

app = FastAPI(title="BOS Light - API Gateway")

# Configuration du CORS pour autoriser le Frontend React (Vite)
origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],  # Autorise toutes les méthodes HTTP (GET, POST, etc.)
    allow_headers=["*"],  # Autorise tous les headers (notamment Authorization: Bearer <token>)
)

# -----------------------------------------------------------------------------
# CONFIGURATION KEYCLOAK (RÉSEAU INTERNE DOCKER VS ACCÈS NAVIGATEUR EXTERNE)
# -----------------------------------------------------------------------------
# URL interne : utilisée par le conteneur FastAPI pour récupérer les certificats JWKS sur le réseau Docker
KEYCLOAK_INTERNAL_URL = os.getenv("KEYCLOAK_INTERNAL_URL", "http://keycloak:8080/realms/bos-realm")

# URL externe : utilisée par la documentation Swagger (/docs) via le navigateur de l'utilisateur
KEYCLOAK_EXTERNAL_URL = os.getenv("KEYCLOAK_EXTERNAL_URL", "http://localhost:8080/realms/bos-realm")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{KEYCLOAK_EXTERNAL_URL}/protocol/openid-connect/token")

# Rôles autorisés à accéder aux fonctions de configuration
ALLOWED_ROLES = {"Administrateur", "Configurateur"}

def get_current_user_roles(token: str = Depends(oauth2_scheme)) -> list:
    """
    Décode le token JWT émis par Keycloak, récupère les clés JWKS via le réseau 
    interne Docker, valide la signature et extrait les rôles de l'utilisateur.
    """
    try:
        # Récupération des clés publiques JWKS de Keycloak via l'adresse interne Docker
        jwks_url = f"{KEYCLOAK_INTERNAL_URL}/protocol/openid-connect/certs"
        jwks = requests.get(jwks_url).json()
        
        # Décodage et vérification de la signature du jeton JWT
        payload = jwt.decode(token, jwks, algorithms=["RS256"], options={"verify_aud": False})
        roles = payload.get("realm_access", {}).get("roles", [])
        return roles
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Jeton d'authentification invalide : {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

def verify_allowed_roles(roles: list = Depends(get_current_user_roles)) -> list:
    """
    Dépendance de sécurité vérifiant si l'utilisateur possède au moins un des rôles autorisés
    (Administrateur ou Configurateur). Rejette le rôle Client avec un code HTTP 403 Forbidden.
    """
    if not set(roles).intersection(ALLOWED_ROLES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé : Seuls les rôles Administrateur et Configurateur sont autorisés."
        )
    return roles

@app.get("/")
def home():
    return {"message": "Bienvenue sur l'API Gateway du BOS Light !"}

@app.get("/api/config-access")
def verify_config_access(roles: list = Depends(verify_allowed_roles)):
    """Vérifie et valide l'accès à l'application de configuration (Admin / Configurateur)."""
    return {
        "status": "success",
        "message": "Accès autorisé à l'application de configuration !",
        "user_roles": roles
    }

# ---------------------------------------------------------------------------
# ÉTAPE 2.3 : ENDPOINTS REST MÉTIER (PHASES & CARTOUCHES WORKFLOW)
# ---------------------------------------------------------------------------
@app.get("/api/v1/phases")
def read_phases(
    db: Session = Depends(get_db), 
    roles: list = Depends(verify_allowed_roles)
):
    """
    Récupère l'ensemble des phases et cartouches enregistrées dans la base de données métier.
    Accès restreint aux utilisateurs autorisés (Administrateur ou Configurateur).
    Si la BDD est vide, procède à l'injection initiale des données de référence (Seeding).
    """
    phases = db.query(PhaseModel).all()
    
    # Seeding initial si la table est vierge lors du premier lancement
    if not phases:
        default_phases = [
            PhaseModel(
                id="A", 
                nom="Phase A", 
                titre="Initialisation & Analyse",
                description="Cadre d'étude préalable et recueil des spécifications initiales.",
                badge_color="#6366f1", 
                cartouche_points=["Audit des besoins", "Cahier des charges", "Validation administrative"],
                etape_nom="Étape 1 : Cadrage", 
                livrables="Rapport d'analyse d'impact, fiche de cadrage validée."
            ),
            PhaseModel(
                id="B", 
                nom="Phase B", 
                titre="Conception & Développement",
                description="Mise en œuvre technique, écriture des spécifications et modélisation.",
                badge_color="#0284c7", 
                cartouche_points=["Écritures techniques", "Maquettage & Schémas", "Revue d'avancement"],
                etape_nom="Flèche Phase B : Action & Validation", 
                livrables="Dossier d'architecture, prototypes validés et écritures."
            ),
            PhaseModel(
                id="C", 
                nom="Phase C", 
                titre="Finalisation & Déploiement",
                description="Recette finale, livraison des éléments et suivi de l'exploitation.",
                badge_color="#10b981", 
                cartouche_points=["Contrôle qualité", "Déploiement final", "Bilan & Clôture"],
                etape_nom="Étape 3 : Clôture", 
                livrables="Document de clôture, PV de recette sans réserve."
            )
        ]
        db.add_all(default_phases)
        db.commit()
        phases = db.query(PhaseModel).all()
        
    return phases