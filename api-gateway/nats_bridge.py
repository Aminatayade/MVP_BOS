"""
FILE: nats_bridge.py
DESCRIPTION: Pont NATS -> WebSocket pour le rafraîchissement temps réel des
             popups de télémétrie équipement (graphe et maquette 3D). Une
             seule connexion NATS partagée, ouverte au démarrage de la
             gateway (voir main.py), qui rediffuse chaque message publié sur
             le même sujet que consume_timeseries.py vers les WebSockets
             abonnées au `global_id` concerné.

IMPORTANT -- abonnement NATS CORE, pas JetStream :
consume_timeseries.py utilise un consumer JetStream PULL avec un nom durable
partagé ("bos-timeseries-consumer", voir adapters/nats_adapter.py). Un second
abonnement JetStream avec ce même nom durable ferait concurrence à ce
consumer pour les messages (chaque message livré à UN SEUL des deux), ce qui
ferait perdre silencieusement des points de mesure côté écriture ArcadeDB.
Un abonnement NATS CORE (`nc.subscribe`, pas `js.pull_subscribe`) reçoit
son propre exemplaire de chaque message publié en parallèle, sans jamais
interférer avec le consumer JetStream qui écrit en base -- exactement le
comportement voulu pour un simple "robinet" de lecture live.

INPUTS:
  - Variables d'environnement NATS_URL / NATS_USER / NATS_PASSWORD /
    BOS_TIMESERIES_SUBJECT (mêmes noms que bim_validator, pour rester
    cohérent avec le reste du système)

OUTPUTS:
  - register(global_id, websocket) / unregister(global_id, websocket) :
    utilisés par la route WebSocket de bim_proxy.py
  - start() / stop() : cycle de vie appelé depuis les événements
    startup/shutdown de main.py
"""

from __future__ import annotations

import json
import os

import nats

from secrets_util import read_secret

DEFAULT_SUBJECT = os.getenv("BOS_TIMESERIES_SUBJECT", "bos.timeseries.>")

_nc = None
_subscription = None
# global_id -> ensemble de WebSockets abonnées à cet équipement. Un même
# équipement peut être ouvert dans plusieurs onglets/popups à la fois.
_subscribers: dict[str, set] = {}


async def start() -> None:
    global _nc, _subscription
    servers = os.getenv("NATS_URL", "nats://nats:4222")
    user = os.getenv("NATS_USER")
    password = read_secret("NATS_PASSWORD")
    _nc = await nats.connect(servers=[servers], user=user, password=password)
    _subscription = await _nc.subscribe(DEFAULT_SUBJECT, cb=_on_message)


def get_connection():
    """Connexion NATS partagée déjà ouverte par start() -- réutilisée par
    aggregation_bridge.py pour une SECONDE souscription core NATS sur cette
    même connexion (widgets de cockpit agrégés), sans ouvrir de nouvelle
    connexion TCP ni interférer avec le consumer JetStream durable (même
    raisonnement que ce module, voir la docstring en tête de fichier).
    `None` si start() n'a pas encore été appelé."""
    return _nc


async def stop() -> None:
    global _nc, _subscription
    if _subscription is not None:
        await _subscription.unsubscribe()
        _subscription = None
    if _nc is not None:
        await _nc.close()
        _nc = None


def register(global_id: str, websocket) -> None:
    _subscribers.setdefault(global_id, set()).add(websocket)


def unregister(global_id: str, websocket) -> None:
    sockets = _subscribers.get(global_id)
    if not sockets:
        return
    sockets.discard(websocket)
    if not sockets:
        _subscribers.pop(global_id, None)


async def _on_message(msg) -> None:
    try:
        payload = json.loads(msg.data.decode("utf-8"))
    except Exception:
        return

    global_id = payload.get("global_id")
    sockets = _subscribers.get(global_id)
    if not sockets:
        return

    # Liste figée : envoyer peut échouer/retirer un socket fermé pendant
    # l'itération, ne jamais muter le set qu'on parcourt directement.
    for websocket in list(sockets):
        try:
            await websocket.send_json(payload)
        except Exception:
            unregister(global_id, websocket)
