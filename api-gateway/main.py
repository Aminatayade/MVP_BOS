"""
FILE: main.py
DESCRIPTION: Serveur principal FastAPI servant d'API Gateway. Autorise le CORS et
             sert les routes métier (Phases/Cartouches, connectées à la BDD
             PostgreSQL applicative) ainsi que le proxy authentifié vers l'API
             BIM (bim_proxy.py). L'authentification Keycloak/RBAC vit dans
             security.py, partagée par toutes les routes protégées.

INPUTS:
  - Token JWT (Header HTTP Authorization: Bearer <token>) — voir security.py
  - Requêtes HTTP provenant de l'application Web React (http://localhost:5173)
  - Connexion BDD Métier (Session SQLAlchemy issue de database.py)

OUTPUTS:
  - Réponses JSON (validation d'accès, données des phases/cartouches métier,
    réponses proxyées de l'API BIM, erreurs HTTP 401/403/502/504)
  - Documentation Swagger interactive générée sur /docs

DEPENDENCIES:
  - fastapi, uvicorn, sqlalchemy, httpx (via bim_proxy), python-jose (via security)
"""

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

# Imports des modules BDD et modèles locaux
from database import engine, get_db, Base
from models import PhaseModel
import bim_proxy
from security import verify_allowed_roles

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

# Dépendances RBAC (JWT Keycloak) : voir security.py — extrait de ce fichier
# pour être réutilisable par bim_proxy.py sans import circulaire.
app.include_router(bim_proxy.router)


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