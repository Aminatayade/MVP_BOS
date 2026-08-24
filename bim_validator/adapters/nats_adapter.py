"""
Couche ADAPTER (NATS JetStream)
--------------------------------
Implémente MessageQueuePort avec nats-py (asyncio). Le métier (application/)
ne voit jamais nats, asyncio, ni la notion de consumer durable : il appelle
publish()/subscribe() sur le port.

Le consumer est DURABLE (nommé, pull-based) : c'est ce qui garantit qu'un
redémarrage du consommateur reprend exactement où il s'était arrêté, sans
perdre les messages publiés pendant qu'il était éteint. C'est la seule
raison de préférer JetStream à NATS core ici.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import nats
from nats.js.errors import APIError

from domain.ports import MessageQueuePort

DEFAULT_STREAM_NAME = "BOS_TIMESERIES"
DEFAULT_SUBJECTS = ["bos.timeseries.>"]
DEFAULT_DURABLE_NAME = "bos-timeseries-consumer"


class NatsJetStreamAdapter(MessageQueuePort):
    def __init__(
        self,
        *,
        servers: str | list[str] = "nats://localhost:4222",
        user: str | None = None,
        password: str | None = None,
        stream_name: str = DEFAULT_STREAM_NAME,
        subjects: list[str] | None = None,
        durable_name: str = DEFAULT_DURABLE_NAME,
    ) -> None:
        self._servers = [servers] if isinstance(servers, str) else list(servers)
        self._user = user
        self._password = password
        self._stream_name = stream_name
        self._subjects = subjects or list(DEFAULT_SUBJECTS)
        self._durable_name = durable_name

    # --- MessageQueuePort ---------------------------------------------

    def publish(self, subject: str, message: dict) -> None:
        asyncio.run(self._publish_many_async(subject, [message]))

    def publish_many(self, subject: str, messages: list[dict]) -> None:
        """Ouvre UNE SEULE connexion pour tout le lot : bien plus efficace
        qu'appeler publish() en boucle, qui reconnecterait à chaque message."""
        asyncio.run(self._publish_many_async(subject, messages))

    def subscribe(self, subject: str, handler: Callable[[dict], None]) -> None:
        """BLOQUANT : boucle infinie de fetch/ack. Prévu pour tourner dans un
        script dédié (consume_timeseries.py), pas dans un serveur web synchrone."""
        asyncio.run(self._subscribe_async(subject, handler))

    # --- Internals -------------------------------------------------------

    async def _connect(self):
        return await nats.connect(servers=self._servers, user=self._user, password=self._password)

    async def _ensure_stream(self, js) -> None:
        try:
            await js.add_stream(name=self._stream_name, subjects=self._subjects)
        except APIError as exc:
            description = (getattr(exc, "description", "") or str(exc)).lower()
            if "already" in description or "in use" in description or "exists" in description:
                return
            raise

    async def _publish_many_async(self, subject: str, messages: list[dict]) -> None:
        nc = await self._connect()
        try:
            js = nc.jetstream()
            await self._ensure_stream(js)
            for message in messages:
                payload = json.dumps(message).encode("utf-8")
                await js.publish(subject, payload)
        finally:
            await nc.close()

    async def _subscribe_async(self, subject: str, handler: Callable[[dict], None]) -> None:
        nc = await self._connect()
        try:
            js = nc.jetstream()
            await self._ensure_stream(js)
            pull_subscription = await js.pull_subscribe(subject, durable=self._durable_name)
            print(
                f"👂 En écoute sur '{subject}' "
                f"(stream='{self._stream_name}', consumer durable='{self._durable_name}')..."
            )
            while True:
                try:
                    messages = await pull_subscription.fetch(batch=10, timeout=5)
                # nats-py lève parfois nats.errors.TimeoutError, parfois
                # directement asyncio.TimeoutError (classe distincte malgré le
                # nom) selon le chemin interne emprunté -- n'attraper que la
                # première laissait le thread mourir silencieusement dès le
                # premier passage sans message, sans jamais redémarrer.
                except (nats.errors.TimeoutError, TimeoutError):
                    continue

                for msg in messages:
                    try:
                        payload = json.loads(msg.data.decode("utf-8"))
                        handler(payload)
                    except Exception as exc:
                        # PAS d'ACK : NATS re-livrera ce message plus tard.
                        print(f"⚠️  Message non acquitté (sera re-livré) : {exc}")
                        continue
                    await msg.ack()
        finally:
            await nc.close()
