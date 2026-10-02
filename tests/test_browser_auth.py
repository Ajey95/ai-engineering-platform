import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app import api, auth, browser_auth
from platform_app.config import Settings
from platform_app.db import Base
from platform_app.models import Project, Tenant, TenantMembership


def test_hosted_browser_code_flow_cookie_scope_and_csrf(monkeypatch):
    config = Settings(
        environment="production",
        database_url="postgresql+psycopg://unused:unused@localhost/unused",
        oidc_issuer="https://issuer.example.test/",
        oidc_audience="aip-api",
        oidc_jwks_url="https://issuer.example.test/keys",
        oidc_client_id="aip-browser",
        oidc_client_secret="confidential-secret",
        oidc_authorization_endpoint="https://issuer.example.test/authorize",
        oidc_token_endpoint="https://issuer.example.test/token",
        browser_session_secret="s" * 48,
        public_base_url="https://app.example.test",
    )
    monkeypatch.setattr(api, "settings", lambda: config)
    monkeypatch.setattr(auth, "settings", lambda: config)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        browser_auth, "_jwks_client", lambda _: SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=key.public_key())
        ),
    )
    nonce = [""]
    calls = []

    def exchange(request):
        calls.append(request)
        claims = {
            "iss": config.oidc_issuer, "aud": config.oidc_client_id,
            "sub": "alice", "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=20),
            "nonce": nonce[0],
        }
        return httpx.Response(200, json={"id_token": jwt.encode(
            claims, key, algorithm="RS256", headers={"kid": "test-key"}
        )})

    token_client = httpx.Client(transport=httpx.MockTransport(exchange))
    original_finish = browser_auth.finish_login
    monkeypatch.setattr(api, "finish_login", lambda *args: original_finish(
        *args, client=token_client
    ))
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant-a", name="A"))
        db.commit()
        db.add_all([
            TenantMembership(tenant_id="tenant-a", subject="alice", role="owner"),
            Project(id="project-a", tenant_id="tenant-a", name="A"),
        ])
        db.commit()

        def session():
            yield db

        api.app.dependency_overrides[api.db_session] = session
        try:
            client = TestClient(api.app, base_url="https://app.example.test")
            assert client.get("/v1/auth/session").status_code == 401
            start = client.get("/v1/auth/start?tenant_id=tenant-a", follow_redirects=False)
            assert start.status_code == 302
            query = parse_qs(urlsplit(start.headers["location"]).query)
            nonce[0] = query["nonce"][0]
            assert query["code_challenge_method"] == ["S256"]
            assert query["redirect_uri"] == ["https://app.example.test/v1/auth/callback"]
            assert "Secure" in start.headers["set-cookie"]
            assert "HttpOnly" in start.headers["set-cookie"]
            assert "SameSite=lax" in start.headers["set-cookie"]
            bad = client.get(
                "/v1/auth/callback?code=test-code&state=wrong", follow_redirects=False
            )
            assert bad.status_code == 401
            callback = client.get(
                f"/v1/auth/callback?code=test-code&state={query['state'][0]}",
                follow_redirects=False,
            )
            assert callback.status_code == 302, callback.text
            assert "SameSite=strict" in callback.headers["set-cookie"]
            session_data = client.get("/v1/auth/session").json()
            assert session_data["tenant_id"] == "tenant-a"
            assert session_data["subject"] == "alice"
            assert len(session_data["csrf"]) == 64
            assert client.get("/v1/projects").json()[0]["id"] == "project-a"
            invalid_header = client.get(
                "/v1/projects", headers={"Authorization": "Basic invalid"}
            )
            assert invalid_header.status_code == 401
            assert client.post("/v1/auth/logout").status_code == 403
            csrf = {"X-AIP-CSRF": session_data["csrf"]}
            assert client.post(
                "/v1/auth/logout", headers={**csrf, "Origin": "https://other.example.test"}
            ).status_code == 403
            assert client.post(
                "/v1/auth/logout", headers={**csrf, "Origin": config.public_base_url}
            ).status_code == 200
            assert client.get("/v1/auth/session").status_code == 401
            assert len(calls) == 1
            body = parse_qs(calls[0].content.decode())
            assert body["code_verifier"] and body["code"] == ["test-code"]
            scheme, credentials = calls[0].headers["Authorization"].split(" ", 1)
            assert scheme == "Basic"
            assert base64.b64decode(credentials).decode() == "aip-browser:confidential-secret"
        finally:
            api.app.dependency_overrides.clear()
    token_client.close()
    engine.dispose()
