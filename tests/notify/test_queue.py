"""Tests for TelegramQueue."""
import pytest
import asyncio
from ipracticom_sweeper.notify import TelegramQueue


@pytest.mark.asyncio
async def test_enqueue_basic():
    q = TelegramQueue(rate_seconds=0.01)
    assert await q.enqueue("hello") is True
    assert q.size == 1


@pytest.mark.asyncio
async def test_drain_calls_sender():
    q = TelegramQueue(rate_seconds=0.01)
    await q.enqueue("a")
    await q.enqueue("b")
    sent_msgs = []
    async def sender(text):
        sent_msgs.append(text)
    n = await q.drain(sender)
    assert n == 2
    assert sent_msgs == ["a", "b"]


@pytest.mark.asyncio
async def test_drop_oldest_when_full():
    q = TelegramQueue(rate_seconds=0.01, max_size=3)
    for i in range(5):
        await q.enqueue(f"m{i}")
    assert q.size == 3
    assert q.stats["dropped"] == 2


@pytest.mark.asyncio
async def test_rate_limit_timing():
    q = TelegramQueue(rate_seconds=0.1)
    await q.enqueue("a")
    await q.enqueue("b")
    times = []
    async def sender(text):
        times.append(asyncio.get_event_loop().time())
    await q.drain(sender)
    assert (times[1] - times[0]) >= 0.1


@pytest.mark.asyncio
async def test_stats():
    q = TelegramQueue(rate_seconds=0.01)
    await q.enqueue("a")
    s = q.stats
    assert s["queued"] == 1
    assert s["sent"] == 0
    assert s["dropped"] == 0
