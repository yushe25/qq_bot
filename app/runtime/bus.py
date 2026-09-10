from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

Subscriber = Callable[[dict[str, Any]], None]

logger = logging.getLogger("qqf.bus")


class EventBus:
    """Small in-process pub/sub used by the scheduler and console.

    Subscribers are synchronous callables so the bus can be used from both
    asyncio tasks and third-party callbacks without requiring an event loop.
    """

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, subscriber: Subscriber) -> None:
        if subscriber not in self._subscribers:
            self._subscribers.append(subscriber)

    def unsubscribe(self, subscriber: Subscriber) -> None:
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)

    def publish(self, event: dict[str, Any]) -> None:
        for subscriber in list(self._subscribers):
            try:
                subscriber(event)
            except Exception:
                logger.warning("event subscriber failed", exc_info=True)
                continue
