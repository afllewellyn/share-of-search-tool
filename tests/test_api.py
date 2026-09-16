"""The Flask edge: what stands between the internet and a paid request.

The pipeline itself is covered by test_web. These tests only exercise the
refusals that happen before it runs — passphrase, rate limit, body shape —
because each of those is what keeps a public URL from spending money.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SOS_WEB_PASSPHRASE", "open-sesame")
    monkeypatch.delenv("DATAFORSEO_LOGIN", raising=False)
    monkeypatch.delenv("DATAFORSEO_USERNAME", raising=False)
    monkeypatch.delenv("DATAFORSEO_PASSWORD", raising=False)
    index = importlib.import_module("index")
    importlib.reload(index)  # fresh limiter per test
    index.app.testing = True
    return index.app.test_client()


def _run(client, passphrase="open-sesame", **kwargs):
    headers = {"X-SOS-Passphrase": passphrase} if passphrase is not None else {}
    return client.post("/api/run", json={"own_brand": {}}, headers=headers, **kwargs)


def test_runs_are_refused_when_no_passphrase_is_configured(client, monkeypatch):
    monkeypatch.delenv("SOS_WEB_PASSPHRASE")
    response = _run(client)
    assert response.status_code == 503
    assert "passphrase" in response.get_json()["error"]


def test_a_missing_or_wrong_passphrase_is_a_401(client):
    assert _run(client, passphrase=None).status_code == 401
    assert _run(client, passphrase="wrong").status_code == 401


def test_the_passphrase_is_checked_before_the_body_is_parsed(client):
    # A valid passphrase reaches the parser, which rejects this body as a 400.
    response = _run(client)
    assert response.status_code == 400
    assert "own_brand" in response.get_json()["error"]


def test_an_address_is_rate_limited(client):
    import index

    for _ in range(index.RUN_LIMIT_PER_ADDRESS):
        assert _run(client).status_code == 400  # accepted by the gate, refused by the parser
    response = _run(client)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == str(index.RUN_LIMIT_WINDOW_SECONDS)


def test_the_address_comes_from_the_proxy_header(client):
    import index

    for i in range(index.RUN_LIMIT_PER_ADDRESS):
        assert client.post("/api/run", json={}, headers={"X-SOS-Passphrase": "open-sesame", "X-Forwarded-For": "203.0.113.1, 10.0.0.1"}).status_code == 400
    blocked = client.post("/api/run", json={}, headers={"X-SOS-Passphrase": "open-sesame", "X-Forwarded-For": "203.0.113.1, 10.0.0.1"})
    other = client.post("/api/run", json={}, headers={"X-SOS-Passphrase": "open-sesame", "X-Forwarded-For": "203.0.113.2"})
    assert blocked.status_code == 429
    assert other.status_code == 400


def test_markets_needs_no_passphrase(client):
    response = client.get("/api/markets")
    assert response.status_code == 200
    assert "US" in response.get_json()["markets"]


def test_unexpected_errors_are_json(client, monkeypatch):
    import index

    def boom(_payload):
        raise RuntimeError("template exploded")

    monkeypatch.setattr(index.web, "parse_request", boom)
    index.app.testing = False
    response = _run(client)
    assert response.status_code == 500
    assert "error" in response.get_json()
    assert "exploded" not in response.get_json()["error"]
