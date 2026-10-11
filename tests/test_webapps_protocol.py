"""webapp-manager: a running sync refuses idle stops; other stops shut the server down."""

import asyncio
import json

import pytest

from sci_report_analyzer import stop, sync


class FakeRequest:
    def __init__(self, body: bytes):
        self.body = body

    async def json(self):
        return json.loads(self.body)


@pytest.fixture
def shutdowns(monkeypatch):
    calls = []
    monkeypatch.setattr(stop.app, "shutdown", lambda: calls.append(True))
    return calls


@pytest.mark.parametrize("running", [False, True])
async def test_idle_stop(monkeypatch, shutdowns, running) -> None:
    monkeypatch.setattr(sync, "any_running", lambda: running)
    assert stop.webapps_status()["busy"] is running
    response = await stop.webapps_shutdown(FakeRequest(b'{"reason": "idle", "deadline": 10}'))
    await asyncio.sleep(0.4)
    assert response.status_code == (409 if running else 202)
    assert shutdowns == ([] if running else [True])


async def test_user_stop_while_syncing(monkeypatch, shutdowns) -> None:
    monkeypatch.setattr(sync, "any_running", lambda: True)
    response = await stop.webapps_shutdown(FakeRequest(b"not json"))
    await asyncio.sleep(0.4)
    assert response.status_code == 202 and shutdowns == [True]
