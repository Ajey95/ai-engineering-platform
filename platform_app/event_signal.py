"""PostgreSQL commit notifications for low latency durable event delivery."""

from __future__ import annotations

import asyncio
import logging
import threading

import psycopg

LOGGER = logging.getLogger(__name__)
CHANNEL = "aip_run_events"


class EventSignal:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        self.listening = False
        self._subscribers: dict[str, set[asyncio.Event]] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self.database_url.startswith("postgresql://"):
            self._task = asyncio.create_task(
                asyncio.to_thread(self._listen_sync, asyncio.get_running_loop())
            )

    async def stop(self) -> None:
        if self._task is not None:
            self._stop.set()
            await self._task
            self._task = None
        self.listening = False

    def subscribe(self, run_id: str) -> asyncio.Event:
        event = asyncio.Event()
        self._subscribers.setdefault(run_id, set()).add(event)
        return event

    def unsubscribe(self, run_id: str, event: asyncio.Event) -> None:
        subscribers = self._subscribers.get(run_id)
        if subscribers is not None:
            subscribers.discard(event)
            if not subscribers:
                del self._subscribers[run_id]

    def notify(self, run_id: str) -> None:
        for event in self._subscribers.get(run_id, ()):
            event.set()

    def _disconnected(self) -> None:
        self.listening = False
        for subscribers in self._subscribers.values():
            for event in subscribers:
                event.set()

    def _listen_sync(self, loop: asyncio.AbstractEventLoop) -> None:
        # Psycopg's async connection does not support the default Windows
        # ProactorEventLoop. One blocking listener thread works on both hosts.
        warned = False
        while not self._stop.is_set():
            try:
                with psycopg.connect(
                    self.database_url, autocommit=True, connect_timeout=3
                ) as connection:
                    connection.execute(f"LISTEN {CHANNEL}")
                    warned = False
                    loop.call_soon_threadsafe(setattr, self, "listening", True)
                    while not self._stop.is_set():
                        for notification in connection.notifies(timeout=0.5):
                            loop.call_soon_threadsafe(self.notify, notification.payload)
            except (OSError, psycopg.Error):
                if not warned:
                    LOGGER.warning(
                        "PostgreSQL event signal unavailable; SSE polling remains active"
                    )
                    warned = True
            finally:
                loop.call_soon_threadsafe(self._disconnected)
            self._stop.wait(1)
