"""The optional FastAPI surface. Skipped entirely if FastAPI isn't installed."""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from labsuite.api import create_app  # noqa: E402
from labsuite.seed import DEMO_PASSWORD, build_lab  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(build_lab()))


def test_token_and_me(client):
    resp = client.post("/oauth/token", json={"username": "anguyen", "password": DEMO_PASSWORD})
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    me = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    body = me.json()
    assert body["identity"]["sub"] == "anguyen"
    assert body["access"]["truenas"].get("research-data") == "modify"


def test_bad_password_rejected(client):
    resp = client.post("/oauth/token", json={"username": "anguyen", "password": "wrong"})
    assert resp.status_code == 401


def test_me_requires_valid_token(client):
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_scim_feed(client):
    resp = client.get("/scim/v2/Users")
    assert resp.status_code == 200
    body = resp.json()
    assert body["totalResults"] == len(body["Resources"]) > 0
    assert body["Resources"][0]["schemas"] == ["urn:ietf:params:scim:schemas:core:2.0:User"]


def test_onboard_offboard_over_http(client):
    resp = client.post(
        "/admin/onboard",
        json={"name": "Nadia Rahman", "department": "Bio", "role": "research-scientist"},
    )
    assert resp.status_code == 200
    username = resp.json()["username"]

    off = client.post("/admin/offboard", json={"username": username})
    assert off.status_code == 200
    assert off.json()["clean"] is True


def test_review_endpoint(client):
    resp = client.get("/review")
    assert resp.status_code == 200
    assert "flags" in resp.json()


def test_dashboard_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "LabSuite" in resp.text
