# FILE: Dockerfile
# DESCRIPTION: Fichier d'instructions pour fabriquer l'image Docker contenant le Frontend React (Vite).
#
# INPUTS:
#   - Image de base Node.js (node:20-alpine)
#   - Fichiers source : package.json, package-lock.json, src/, public/
#
# OUTPUTS:
#   - Image Docker prête à exécuter le serveur de développement Vite sur le port 5173.
#
# DEPENDENCIES:
#   - Docker Engine / Docker Desktop
#   - Node.js 20 et packages npm (react, keycloak-js, axios, etc.)
#
# NOTES / PRÉREQUIS:
#   - Le drapeau '--host' dans la commande CMD est indispensable pour que Vite soit accessible depuis l'extérieur du conteneur.

FROM node:20-alpine

WORKDIR /app

# Copie des fichiers de configuration d'environnement Node
COPY package*.json ./

# Installation des dépendances NPM
RUN npm install

# Copie de l'intégralité du code source React
COPY . .

# Documentation du port d'écoute interne de Vite
EXPOSE 5173

# Commande de lancement du serveur de développement Vite
CMD ["npm", "run", "dev", "--", "--host"]