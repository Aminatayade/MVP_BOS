"""
FILE: aggregation_bridge.py
DESCRIPTION: Pont NATS -> recalcul AGRÉGÉ pour les widgets de cockpit (BOS
             Exécution) -- distinct de nats_bridge.py (qui relaie par
             global_id EXACT, pour la popup équipement). Ici, chaque
             connexion enregistrée porte un ENSEMBLE d'équipements (le
             périmètre spatial résolu du widget : espace/étage/pièce) et un
             callback à déclencher, DÉBOUNCÉ, dès qu'un message touche AU
             MOINS UN équipement de cet ensemble -- évite un recalcul par
             point individuel quand plusieurs équipements du même widget
             sont mis à jour dans la même rafale.

Partage la MÊME connexion NATS déjà ouverte par nats_bridge
(nats_bridge.get_connection()) -- une SECONDE souscription core NATS
dessus, pas une nouvelle connexion TCP, même raisonnement d'isolation
vis-à-vis du consumer JetStream durable que nats_bridge.py (voir sa
docstring : un abonnement core NATS reçoit son propre exemplaire de chaque
message, sans jamais faire concurrence au consumer JetStream qui écrit en
base).

OUTPUTS:
  - register(conn_id, equipment_ids, on_hit) / unregister(conn_id) :
    utilisés par la route WebSocket /ws/widget-aggregate de bim_proxy.py
  - start() / stop() : cycle de vie, appelé APRÈS nats_bridge.start()/AVANT
    nats_bridge.stop() dans main.py (dépend de sa connexion déjà ouverte)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

import nats_bridge

DEBOUNCE_SECONDS = 2.0

_subscription = None
# conn_id -> {"equipment_ids": set[str], "on_hit": Callable[[], Awaitable[None]], "debounce_task": asyncio.Task | None}
_connections: dict[str, dict] = {}


async def start() -> None:
    """À appeler APRÈS nats_bridge.start() (dépend de sa connexion déjà
    ouverte). Best-effort comme nats_bridge -- une erreur ici ne doit pas
    empêcher la gateway de démarrer, seulement priver les cockpits de push
    temps réel (repli possible : polling côté frontend)."""
    global _subscription
    nc = nats_bridge.get_connection()
    if nc is None:
        raise RuntimeError("nats_bridge n'est pas connecté -- appeler aggregation_bridge.start() après nats_bridge.start().")
    _subscription = await nc.subscribe(nats_bridge.DEFAULT_SUBJECT, cb=_on_message)


async def stop() -> None:
    global _subscription
    if _subscription is not None:
        await _subscription.unsubscribe()
        _subscription = None
    for entry in list(_connections.values()):
        task = entry.get("debounce_task")
        if task is not None:
            task.cancel()
    _connections.clear()


def register(conn_id: str, equipment_ids: set[str], on_hit: Callable[[], Awaitable[None]]) -> None:
    _connections[conn_id] = {"equipment_ids": equipment_ids, "on_hit": on_hit, "debounce_task": None}


def unregister(conn_id: str) -> None:
    entry = _connections.pop(conn_id, None)
    if entry is not None and entry.get("debounce_task") is not None:
        entry["debounce_task"].cancel()


async def _on_message(msg) -> None:
    try:
        payload = json.loads(msg.data.decode("utf-8"))
    except Exception:
        return
    global_id = payload.get("global_id")
    if not global_id:
        return

    # Liste figée : _schedule_debounced_hit peut, via un hit précédent déjà
    # résolu, avoir modifié _connections entre-temps.
    for conn_id, entry in list(_connections.items()):
        if global_id in entry["equipment_ids"]:
            _schedule_debounced_hit(conn_id, entry)


def _schedule_debounced_hit(conn_id: str, entry: dict) -> None:
    """Une nouvelle correspondance dans la fenêtre de debounce annule et
    relance le délai -- regroupe une rafale de plusieurs équipements du même
    widget en un seul recalcul, au lieu d'un appel HTTP interne par point
    individuel reçu."""
    existing_task = entry.get("debounce_task")
    if existing_task is not None:
        existing_task.cancel()

    async def _wait_and_fire() -> None:
        try:
            await asyncio.sleep(DEBOUNCE_SECONDS)
        except asyncio.CancelledError:
            return
        current = _connections.get(conn_id)
        if current is None:
            return
        try:
            await current["on_hit"]()
        except Exception as exc:
            print(f"⚠️  Recalcul agrégé du widget en échec (conn={conn_id}) : {exc}")

    entry["debounce_task"] = asyncio.create_task(_wait_and_fire())
