"""健康检查：file 不可达只降级，不返回 503。"""

from __future__ import annotations


def test_health_ok(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["file"]["ok"] is True


def test_file_down_is_degraded(client, fake_file):
    fake_file.health_ok = False
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["file"]["ok"] is False
