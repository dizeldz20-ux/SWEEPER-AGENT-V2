"""Telegram rate-limit queue: 1 msg / 2s, drop-oldest at 1000."""
from __future__ import annotations
import asyncio
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class QueuedMessage:
    text: str
    enqueued_at: float


class TelegramQueue:
    def __init__(self, rate_seconds: float = 2.0, max_size: int = 1000):
        self.rate = rate_seconds
        self.max_size = max_size
        self._queue: deque[QueuedMessage] = deque()
        self._sent_count = 0
        self._dropped_count = 0
        self._lock = asyncio.Lock()

    async def enqueue(self, text: str) -> bool:
        """Add message to queue. Returns False if dropped (queue full)."""
        async with self._lock:
            if len(self._queue) >= self.max_size:
                self._queue.popleft()  # drop oldest
                self._dropped_count += 1
            self._queue.append(QueuedMessage(text=text, enqueued_at=time.time()))
            return True

    async def drain(self, sender) -> int:
        """Drain queue using async sender. Returns number sent.

        sender: async callable(text: str) -> bool
        """
        sent = 0
        async with self._lock:
            while self._queue:
                msg = self._queue.popleft()
                try:
                    await sender(msg.text)
                    sent += 1
                    self._sent_count += 1
                except Exception:
                    # re-enqueue at front
                    self._queue.appendleft(msg)
                    break
                # rate limit
                await asyncio.sleep(self.rate)
        return sent

    @property
    def size(self) -> int:
        return len(self._queue)

    @property
    def stats(self) -> dict:
        return {
            "queued": len(self._queue),
            "sent": self._sent_count,
            "dropped": self._dropped_count,
        }
