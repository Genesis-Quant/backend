from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from apps.users.models import User
from apps.users.services import verify_password
from apps.users.views import router as users_router
from config.database import Base, get_database_session


@pytest.fixture
def auth_client(monkeypatch) -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    monkeypatch.setenv("ARENA_JWT_SECRET", "test-jwt-secret-with-at-least-32-characters")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS arena_backend")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def database_session() -> Generator[Session, None, None]:
        with sessions() as session:
            yield session

    application = FastAPI()
    application.include_router(users_router)
    application.dependency_overrides[get_database_session] = database_session
    with TestClient(application) as client:
        yield client, sessions
    engine.dispose()


def test_register_login_and_current_user(auth_client):
    client, sessions = auth_client
    registration = client.post("/api/v1/auth/register", json={"username": "Arena_User", "password": "secure-password"})

    assert registration.status_code == 201
    registered = registration.json()
    assert registered["token_type"] == "bearer"
    assert registered["user"]["username"] == "arena_user"
    assert "password_hash" not in registered["user"]

    with sessions() as session:
        user = session.scalar(select(User).where(User.username == "arena_user"))
        assert user is not None
        assert user.password_hash != "secure-password"
        assert verify_password("secure-password", user.password_hash)

    current = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {registered['access_token']}"})
    assert current.status_code == 200
    assert current.json()["id"] == registered["user"]["id"]

    login = client.post("/api/v1/auth/login", json={"username": "ARENA_USER", "password": "secure-password"})
    assert login.status_code == 200
    assert login.json()["user"]["username"] == "arena_user"


def test_password_whitespace_is_preserved(auth_client):
    client = auth_client[0]
    payload = {"username": "space_user", "password": " password "}

    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    assert client.post("/api/v1/auth/login", json=payload).status_code == 200
    assert client.post("/api/v1/auth/login", json={**payload, "password": "password"}).status_code == 401


def test_duplicate_user_and_invalid_credentials(auth_client):
    client = auth_client[0]
    payload = {"username": "arena_user", "password": "secure-password"}

    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    assert client.post("/api/v1/auth/register", json=payload).status_code == 409
    assert client.post("/api/v1/auth/login", json={**payload, "password": "wrong-password"}).status_code == 401
    assert client.get("/api/v1/users/me").status_code == 401


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "ab", "password": "secure-password"},
        {"username": "invalid-name", "password": "secure-password"},
        {"username": "arena", "password": "short"},
        {"username": "arena", "password": "密码" * 25},
    ],
)
def test_registration_validates_credentials(auth_client, payload):
    client = auth_client[0]
    assert client.post("/api/v1/auth/register", json=payload).status_code == 422
