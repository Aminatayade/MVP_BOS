# FILE: Dockerfile
# DESCRIPTION: Fichier d'instructions pour fabriquer l'image Docker contenant l'API Gateway FastAPI.
#
# INPUTS:
#   - Image de base Python (python:3.11-slim)
#   - Fichiers source : requirements.txt, main.py, dossiers de code backend
#
# OUTPUTS:
#   - Image Docker prête à exécuter le serveur Uvicorn / FastAPI sur le port 8000.
#
# DEPENDENCIES:
#   - Docker Engine / Docker Desktop
#   - Python 3.11 et dépendances répertoriées dans requirements.txt
#
# NOTES / PRÉREQUIS:
#   - Exécuter la commande 'docker build' ou passer par 'docker compose up --build'.

FROM python:3.11-slim

WORKDIR /app

# Copie et installation des dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie de l'intégralité des fichiers sources
COPY . .

# Documentation du port d'écoute interne du conteneur
EXPOSE 8000

# Commande de lancement du serveur Uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]