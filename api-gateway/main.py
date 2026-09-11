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

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

# Imports des modules BDD et modèles locaux
from database import engine, get_db, Base
from models import PhaseModel, CockpitModel
from schemas import CockpitIn
import bim_proxy
import nats_bridge
import aggregation_bridge
from security import get_current_user, verify_allowed_roles, verify_execution_allowed_roles

# Génération / Vérification de la présence des tables en BDD au démarrage
Base.metadata.create_all(bind=engine)

app = FastAPI(title="BOS Light - API Gateway")

# Configuration du CORS pour autoriser les deux frontends React (Vite) :
# BOS Configurateur (5173) et BOS Exécution (5174), tous deux servis par
# cette même API Gateway partagée.
origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
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


@app.on_event("startup")
async def _start_nats_bridge() -> None:
    # Best-effort : NATS peut ne pas être prêt au tout premier démarrage
    # (ordre de dépendances Docker Compose) -- ne pas empêcher la gateway de
    # démarrer pour autant, le rafraîchissement temps réel des popups de
    # télémétrie est une amélioration, pas une fonctionnalité bloquante.
    try:
        await nats_bridge.start()
    except Exception as exc:
        print(f"⚠️  Pont NATS->WebSocket indisponible au démarrage : {exc}")
        return

    # aggregation_bridge dépend de la connexion déjà ouverte par
    # nats_bridge.start() ci-dessus -- ne tenter que si celui-ci a réussi.
    try:
        await aggregation_bridge.start()
    except Exception as exc:
        print(f"⚠️  Pont NATS->agrégation cockpit indisponible au démarrage : {exc}")


@app.on_event("shutdown")
async def _stop_nats_bridge() -> None:
    await aggregation_bridge.stop()
    await nats_bridge.stop()


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

@app.get("/api/execution-access")
def verify_execution_access(roles: list = Depends(verify_execution_allowed_roles)):
    """Vérifie et valide l'accès à l'application BOS Exécution (Admin / Configurateur / Client)."""
    return {
        "status": "success",
        "message": "Accès autorisé à l'application BOS Exécution !",
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

# ---------------------------------------------------------------------------
# BIBLIOTHÈQUE DE COCKPITS (BOS Exécution) -- compositions de widgets
# sauvegardées par les utilisateurs. verify_execution_allowed_roles (pas
# verify_allowed_roles) : le rôle Client doit pouvoir créer/posséder ses
# propres cockpits, c'est le cœur de la fonctionnalité.
# ---------------------------------------------------------------------------
@app.get("/api/v1/cockpits")
def list_cockpits(
    db: Session = Depends(get_db),
    roles: list = Depends(verify_execution_allowed_roles),
):
    """Renvoie TOUS les cockpits visibles (mes cockpits + partagés par
    l'équipe) -- le tri "Mes cockpits"/"Partagés" se fait côté frontend en
    comparant owner_username à l'utilisateur courant, pas de filtre serveur
    (pas de notion de cockpit privé/non partagé dans ce modèle)."""
    return db.query(CockpitModel).order_by(CockpitModel.updated_at.desc()).all()


@app.post("/api/v1/cockpits")
def create_cockpit(
    body: CockpitIn,
    db: Session = Depends(get_db),
    roles: list = Depends(verify_execution_allowed_roles),
    user: dict = Depends(get_current_user),
):
    cockpit = CockpitModel(owner_username=user["username"], **body.dict())
    db.add(cockpit)
    db.commit()
    db.refresh(cockpit)
    return cockpit


@app.put("/api/v1/cockpits/{cockpit_id}")
def update_cockpit(
    cockpit_id: str,
    body: CockpitIn,
    db: Session = Depends(get_db),
    roles: list = Depends(verify_execution_allowed_roles),
    user: dict = Depends(get_current_user),
):
    cockpit = db.query(CockpitModel).filter(CockpitModel.id == cockpit_id).first()
    if not cockpit:
        raise HTTPException(status_code=404, detail="Cockpit introuvable.")
    if cockpit.owner_username != user["username"]:
        raise HTTPException(status_code=403, detail="Seul le créateur peut modifier ce cockpit.")
    for key, value in body.dict().items():
        setattr(cockpit, key, value)
    db.commit()
    db.refresh(cockpit)
    return cockpit


@app.delete("/api/v1/cockpits/{cockpit_id}")
def delete_cockpit(
    cockpit_id: str,
    db: Session = Depends(get_db),
    roles: list = Depends(verify_execution_allowed_roles),
    user: dict = Depends(get_current_user),
):
    cockpit = db.query(CockpitModel).filter(CockpitModel.id == cockpit_id).first()
    if not cockpit:
        raise HTTPException(status_code=404, detail="Cockpit introuvable.")
    if cockpit.owner_username != user["username"]:
        raise HTTPException(status_code=403, detail="Seul le créateur peut supprimer ce cockpit.")
    db.delete(cockpit)
    db.commit()
    return {"ok": True}