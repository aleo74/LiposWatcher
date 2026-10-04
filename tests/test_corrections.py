import csv
import io

import pytest
from fastapi.testclient import TestClient

from backend import db
import backend.app as app_module
from backend.logic import entry_metrics


@pytest.fixture
def client(tmp_path, monkeypatch):
    with TestClient(app_module.app) as test_client:
        yield test_client


def create_account(client, name="pilot"):
    assert client.post("/api/auth/setup", json={"username": name, "password": "test_password_123"}).status_code == 201


def create_battery(client, **extra):
    payload = {"number": "001", "chemistry": "LiPo", "cells": 3, "nominal_mah": 1300, **extra}
    result = client.post("/api/batteries", json=payload)
    assert result.status_code == 201, result.text
    return result.json()["id"]


def test_login_limit_and_expired_session(client, monkeypatch):
    create_account(client)
    monkeypatch.setattr(app_module, "LOGIN_MAX_ATTEMPTS", 3)
    for _ in range(3):
        assert client.post("/api/auth/login", json={"username": "pilot", "password": "wrong_password_123"}).status_code == 401
    blocked = client.post("/api/auth/login", json={"username": "pilot", "password": "test_password_123"})
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0
    with db.connect() as connection:
        connection.execute("DELETE FROM login_attempts")
    assert client.post("/api/auth/login", json={"username": "pilot", "password": "test_password_123"}).status_code == 200
    battery_id = create_battery(client)
    with db.connect() as connection:
        connection.execute("DELETE FROM login_sessions")
    payload = {"kind": "charge", "added_mah": 780}
    assert client.post(f"/api/batteries/{battery_id}/entries", json=payload).status_code == 401
    assert client.post("/api/auth/login", json={"username": "pilot", "password": "test_password_123"}).status_code == 200
    assert client.post(f"/api/batteries/{battery_id}/entries", json=payload).status_code == 201


def test_guide_and_cell_gaps_isolated(client):
    create_account(client)
    assert client.post("/api/batteries", json={"number": "bad", "chemistry": "LiPo", "cells": 3, "nominal_mah": 1300, "charge_c": 1}).status_code == 422
    assert client.post("/api/batteries", json={"number": "bad", "chemistry": "LiPo", "cells": 3, "nominal_mah": 1300, "manufacturer_guide_url": "https://example.com/manual"}).status_code == 422
    battery_id = create_battery(client, charge_c=1, charge_rate_source="Notice du pack")
    assert client.put(f"/api/batteries/{battery_id}/guide/inspection", json={"done": True}).status_code == 200
    assert client.put(f"/api/batteries/{battery_id}/guide/first_sessions", json={"done": True}).status_code == 200
    result = client.post(f"/api/batteries/{battery_id}/entries", json={"kind": "baseline", "before_v": [3.7, None, 3.72], "resistance_mohm": [5, None, 7], "charger": "D2 Mark II", "ambient_c": 20, "notes": "Mesure initiale"})
    assert result.status_code == 201, result.text
    data = client.get(f"/api/batteries/{battery_id}").json()
    assert data["guide_steps"]["inspection"]
    assert data["entries"][0]["before_v"] == [3.7, None, 3.72]
    assert data["entries"][0]["resistance_mohm"] == [5, None, 7]
    assert data["entries"][0]["metrics"]["resistance_mean_mohm"] == 6
    assert client.post("/api/users", json={"username": "other", "password": "other_password_123"}).status_code == 201
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": "other", "password": "other_password_123"}).status_code == 200
    assert client.get(f"/api/batteries/{battery_id}").status_code == 404
    assert client.put(f"/api/batteries/{battery_id}/guide/inspection", json={"done": False}).status_code == 404
    assert client.get(f"/api/batteries/{battery_id}/qr.png").status_code == 404


def test_charge_limits_are_finite_and_manufacturer_urls_are_http(client):
    create_account(client)
    base = {"number": "001", "chemistry": "LiHV", "cells": 4, "nominal_mah": 1300}
    for field in ("charge_c", "c_rating"):
        for value in ("NaN", "Infinity", "-Infinity", -1, 0):
            response = client.post("/api/batteries", json={**base, field: value, "charge_rate_source": "Notice du pack"})
            assert response.status_code == 422, (field, value, response.text)
    for url in ("javascript:alert(1)", "ftp://example.com/manual", "https://", "https://user:pass@example.com/manual", "http://exa mple.com/manual"):
        response = client.post("/api/batteries", json={**base, "manufacturer_guide_url": url, "manufacturer_guide_scope": "Référence exacte"})
        assert response.status_code == 422, (url, response.text)
    created = client.post("/api/batteries", json={**base, "charge_c": 1.5, "charge_rate_source": "Notice référence A", "manufacturer_guide_url": "https://example.com/manual", "manufacturer_guide_scope": "Référence A"})
    assert created.status_code == 201, created.text
    http_source = client.post("/api/batteries", json={**base, "number": "002", "manufacturer_guide_url": "http://example.com/manual", "manufacturer_guide_scope": "Référence B"})
    assert http_source.status_code == 201, http_source.text
    battery_id = created.json()["id"]
    for payload in ({"kind": "baseline", "current_a": "NaN"}, {"kind": "baseline", "before_v": [3.7, "Infinity", 3.8, 3.8]}, {"kind": "baseline", "resistance_mohm": [5, 6, -1, 7]}):
        assert client.post(f"/api/batteries/{battery_id}/entries", json=payload).status_code == 422
    valid = client.post(f"/api/batteries/{battery_id}/entries", json={"kind": "baseline", "initial_percent": 50, "initial_percent_source": "Étiquette du pack", "charger": "Chargeur A", "ambient_c": 20})
    assert valid.status_code == 201, valid.text
    assert valid.json()["initial_percent"] == 50


def test_csv_formula_escape_and_qr_payload(client, monkeypatch):
    create_account(client)
    battery_id = create_battery(client, brand="=HYPERLINK(\"bad\")", notes="  +SUM(1,2)")
    client.post(f"/api/batteries/{battery_id}/entries", json={"kind": "charge", "added_mah": 100, "charger": "@formula", "before_v": [3.7, None, 3.8]})
    batteries = list(csv.DictReader(io.StringIO(client.get("/api/export/batteries.csv").text.lstrip("\ufeff"))))
    entries = list(csv.DictReader(io.StringIO(client.get("/api/export/entries.csv").text.lstrip("\ufeff"))))
    assert batteries[0]["brand"].startswith("'=")
    assert batteries[0]["notes"].startswith("'  +")
    assert entries[0]["charger"].startswith("'@")
    assert entries[0]["before_v"] == "[3.7, null, 3.8]"
    seen = []

    class FakeImage:
        def save(self, output, format):
            output.write(b"fake PNG")

    monkeypatch.setattr(app_module.qrcode, "make", lambda value: (seen.append(value), FakeImage())[1])
    assert client.get(f"/api/batteries/{battery_id}/qr.png").status_code == 200
    assert seen == [f"lipowatcher:v1:{battery_id}"]


def test_above_nominal_values_remain_visible():
    measured = entry_metrics({"kind": "test", "discharged_mah": 1400}, 1300)
    estimated = entry_metrics({"kind": "charge", "added_mah": 900, "initial_percent": 40, "final_percent": 100}, 1300)
    assert measured["measured_ratio"] == 107.7
    assert measured["measured_mah"] == 1400
    assert "pas automatiquement invalide" in measured["above_nominal_caution"]
    assert estimated["estimated_mah"] == 1500
    assert "pas automatiquement invalide" in estimated["above_nominal_caution"]
    assert entry_metrics({"kind": "charge", "added_mah": 780, "initial_percent": 40, "final_percent": 100}, 1300)["estimated_mah"] == 1300
    assert entry_metrics({"kind": "charge", "added_mah": 780, "initial_percent": 40, "final_percent": 80}, 1300)["estimated_mah"] == 1950
    assert entry_metrics({"kind": "test", "discharged_mah": 1050}, 1300)["measured_ratio"] == 80.8


