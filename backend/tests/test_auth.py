from fastapi.testclient import TestClient

from app.api import deps
from app.main import app


def test_register_login_and_me(db_session):
    client = TestClient(app)
    app.dependency_overrides[deps.get_db] = lambda: db_session
    try:
        register_resp = client.post(
            "/api/auth/register",
            json={"email": "user@example.com", "password": "supersecret123", "full_name": "Test User"},
        )
        assert register_resp.status_code == 201
        token = register_resp.json()["access_token"]

        duplicate_resp = client.post(
            "/api/auth/register",
            json={"email": "user@example.com", "password": "supersecret123"},
        )
        assert duplicate_resp.status_code == 409

        login_resp = client.post(
            "/api/auth/login", json={"email": "user@example.com", "password": "supersecret123"}
        )
        assert login_resp.status_code == 200

        wrong_login_resp = client.post(
            "/api/auth/login", json={"email": "user@example.com", "password": "wrongpassword"}
        )
        assert wrong_login_resp.status_code == 401

        me_resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me_resp.status_code == 200
        assert me_resp.json()["email"] == "user@example.com"

        unauthenticated_resp = client.get("/api/auth/me")
        assert unauthenticated_resp.status_code == 401
    finally:
        app.dependency_overrides.clear()
