import pytest
from fastapi.testclient import TestClient

from backend import db
from backend.app import app
from backend.logic import entry_metrics


@pytest.fixture
def client(tmp_path, monkeypatch):
    with TestClient(app) as client:
        yield client


def signup(client, username):
    return client.post("/api/auth/setup", json={"username": username, "password": "long_password_123"})


def battery(number="001"):
    return {"number": number, "chemistry": "LiPo", "cells": 4, "nominal_mah": 5000, "condition": "used", "prior_history": "unknown"}


def test_setup_search_session_correction_and_persistence(client):
    assert client.get("/api/auth/setup").json() == {"needed": True}
    assert signup(client, "pilot").status_code == 201
    assert client.get("/api/auth/setup").json() == {"needed": False}
    assert signup(client, "another").status_code == 403
    response = client.post("/api/batteries", json=battery())
    assert response.status_code == 201
    bid = response.json()["id"]
    assert client.post("/api/batteries", json=battery()).status_code == 409
    assert [b["number"] for b in client.get("/api/batteries?q=001").json()] == ["001"]
    assert client.get("/api/batteries?q=1").json()[0]["number"] == "001"
    entry = client.post(f"/api/batteries/{bid}/entries", json={"kind": "charge", "added_mah": 2100, "initial_percent": 40, "final_percent": 100, "before_v": [3.7, 3.71, 3.7, 3.72]}).json()
    assert entry["metrics"]["estimated_mah"] == 3500
    assert entry["metrics"]["before_delta_v"] == 0.02
    eid = entry["id"]
    assert client.post(f"/api/batteries/{bid}/entries", json={"kind": "charge", "added_mah": 10, "before_v": [3.8]}).status_code == 422
    changed = client.put(f"/api/batteries/{bid}/entries/{eid}", json={"kind": "charge", "added_mah": 2200}).json()
    assert changed["added_mah"] == 2200
    detail = client.get(f"/api/batteries/{bid}").json()
    assert detail["charge_count"] == 1
    assert detail["total_added_mah"] == 2200
    assert detail["condition"] == "used"
    assert detail["prior_cycles"] is None
    assert client.get("/api/export/batteries.csv").status_code == 200
    assert client.get(f"/api/batteries/{bid}/qr.png").headers["content-type"] == "image/png"
    with TestClient(app) as restarted:
        assert restarted.post("/api/auth/login", json={"username": "pilot", "password": "long_password_123"}).status_code == 200
        assert restarted.get(f"/api/batteries/{bid}").json()["total_added_mah"] == 2200
    assert client.delete(f"/api/batteries/{bid}/entries/{eid}").status_code == 204
    assert client.get(f"/api/batteries/{bid}").json()["charge_count"] == 0


def test_user_isolation(client):
    signup(client, "admin")
    bid = client.post("/api/batteries", json=battery()).json()["id"]
    eid = client.post(f"/api/batteries/{bid}/entries", json={"kind": "charge", "added_mah": 100}).json()["id"]
    assert client.post("/api/users", json={"username": "pilot2", "password": "long_password_456"}).status_code == 201
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/batteries").status_code == 401
    assert client.post("/api/auth/login", json={"username": "pilot2", "password": "long_password_456"}).status_code == 200
    assert client.get("/api/batteries").json() == []
    assert client.get(f"/api/batteries/{bid}").status_code == 404
    assert client.get(f"/api/batteries/{bid}/qr.png").status_code == 404
    assert client.put(f"/api/batteries/{bid}/entries/{eid}", json={"kind":"charge","added_mah":999}).status_code == 404
    assert client.delete(f"/api/batteries/{bid}/entries/{eid}").status_code == 404
    assert "001" not in client.get("/api/export/batteries.csv").text
    assert client.post("/api/batteries", json=battery()).status_code == 201
    assert client.post("/api/users", json={"username": "pilot3", "password": "long_password_789"}).status_code == 403


def test_batch_is_atomic(client):
    signup(client, "admin")
    payload = {"numbers": ["001", "002", "003"], "template": battery()}
    response = client.post("/api/batteries/batch", json=payload)
    assert response.status_code == 201
    assert [b["number"] for b in client.get("/api/batteries").json()] == ["001", "002", "003"]
    assert client.post("/api/batteries/batch", json={"numbers": ["004", "002"], "template": battery()}).status_code == 409
    assert [b["number"] for b in client.get("/api/batteries").json()] == ["001", "002", "003"]


def test_capacity_rules():
    assert entry_metrics({"kind": "test", "discharged_mah": 4000, "added_mah": None}, 5000)["measured_ratio"] == 80
    assert "estimated_mah" not in entry_metrics({"kind": "charge", "added_mah": 200, "initial_percent": 95, "final_percent": 100}, 5000)
    assert "estimated_mah" not in entry_metrics({"kind": "charge", "added_mah": 200, "initial_percent": None, "final_percent": 100}, 5000)
    assert entry_metrics({"kind": "charge", "added_mah": 2500, "initial_percent": 50, "final_percent": 100}, 5000)["estimated_mah"] == 5000
