"""Hosted OIDC code flow and opaque, server-side browser sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken
from jwt.exceptions import PyJWTError
from sqlalchemy.orm import Session

from platform_app.auth import _jwks_client, require_tenant_membership
from platform_app.config import Settings
from platform_app.db import utcnow
from platform_app.models import BrowserSession
from platform_app.service import ServiceError

SESSION_COOKIE = "__Host-aip_session"
LOGIN_COOKIE = "__Host-aip_login"
SESSION_HOURS = 8
LOGIN_MINUTES = 5


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
        or parsed.fragment or parsed.query or parsed.port not in {None, 443}
    ):
        raise ValueError("Hosted browser auth requires reviewed HTTPS URLs")
    hostname = parsed.hostname.lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Hosted browser auth cannot use a local URL")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("Hosted browser auth requires DNS hostnames")
    return f"https://{hostname}"


def validate_browser_auth_config(config: Settings) -> str:
    if (
        not config.oidc_client_id or not config.oidc_client_secret
        or len(config.browser_session_secret.encode()) < 32
    ):
        raise ValueError("OIDC browser client and session secret are required")
    public_origin = _origin(config.public_base_url)
    if config.public_base_url.rstrip("/") != public_origin:
        raise ValueError("Public browser URL must be an HTTPS origin")
    _origin(config.oidc_authorization_endpoint)
    _origin(config.oidc_token_endpoint)
    return public_origin


def _cipher(config: Settings) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(
        config.browser_session_secret.encode("utf-8")
    ).digest())
    return Fernet(key)


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def start_login(
    config: Settings, tenant_id: str, *, now: datetime | None = None,
) -> tuple[str, str]:
    public_origin = validate_browser_auth_config(config)
    if not 1 <= len(tenant_id) <= 36 or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-"
                                          for c in tenant_id.lower()):
        raise ServiceError("INVALID_TENANT", "Workspace identifier is invalid", 400)
    now = now or utcnow()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    nonce = secrets.token_urlsafe(32)
    login_state = {
        "state": state, "verifier": verifier, "nonce": nonce,
        "tenant_id": tenant_id,
        "expires_at": int((now + timedelta(minutes=LOGIN_MINUTES)).timestamp()),
    }
    cookie = _cipher(config).encrypt(json.dumps(
        login_state, separators=(",", ":")
    ).encode()).decode()
    query = urlencode({
        "response_type": "code", "client_id": config.oidc_client_id,
        "redirect_uri": f"{public_origin}/v1/auth/callback", "scope": "openid",
        "state": state, "nonce": nonce,
        "code_challenge": _challenge(verifier), "code_challenge_method": "S256",
    })
    return f"{config.oidc_authorization_endpoint}?{query}", cookie


def _verified_id_claims(token: str, config: Settings, nonce: str) -> dict:
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise ValueError("Unsupported ID token header")
        key = _jwks_client(config.oidc_jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, key.key, algorithms=["RS256"], audience=config.oidc_client_id,
            issuer=config.oidc_issuer, leeway=30,
            options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
        )
        audiences = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
        if len(audiences) > 1 and claims.get("azp") != config.oidc_client_id:
            raise ValueError("ID token authorized party mismatch")
        if not hmac.compare_digest(claims["nonce"], nonce):
            raise ValueError("ID token nonce mismatch")
        subject = claims["sub"]
        if not isinstance(subject, str) or not 1 <= len(subject) <= 200:
            raise ValueError("ID token subject is invalid")
        return claims
    except (PyJWTError, ValueError, KeyError, TypeError) as error:
        raise ServiceError("UNAUTHENTICATED", "OIDC ID token is invalid", 401) from error


def finish_login(
    db: Session, config: Settings, encrypted_state: str | None,
    state: str, code: str, *, client: httpx.Client | None = None,
    now: datetime | None = None,
) -> tuple[str, BrowserSession]:
    public_origin = validate_browser_auth_config(config)
    now = now or utcnow()
    if not encrypted_state or len(encrypted_state) > 4096 or not 1 <= len(code) <= 4096:
        raise ServiceError("UNAUTHENTICATED", "Login state is missing", 401)
    try:
        saved = json.loads(_cipher(config).decrypt(encrypted_state.encode()))
        if (
            not isinstance(saved, dict)
            or not isinstance(saved.get("state"), str)
            or not hmac.compare_digest(saved["state"], state)
            or int(saved["expires_at"]) <= int(now.timestamp())
        ):
            raise ValueError("Login state mismatch")
        verifier = saved["verifier"]
        nonce = saved["nonce"]
        tenant_id = saved["tenant_id"]
        if not all(isinstance(value, str) for value in (verifier, nonce, tenant_id)):
            raise ValueError("Login state is malformed")
    except (InvalidToken, ValueError, KeyError, TypeError) as error:
        raise ServiceError("UNAUTHENTICATED", "Login state is invalid", 401) from error
    owned_client = client is None
    client = client or httpx.Client()
    try:
        try:
            response = client.post(
                config.oidc_token_endpoint,
                data={
                    "grant_type": "authorization_code", "code": code,
                    "redirect_uri": f"{public_origin}/v1/auth/callback",
                    "code_verifier": verifier,
                },
                auth=(config.oidc_client_id, config.oidc_client_secret),
                timeout=10, follow_redirects=False,
            )
            if response.status_code != 200:
                raise ValueError("Token exchange rejected")
            body = response.json()
            id_token = body["id_token"]
            if not isinstance(id_token, str) or len(id_token) > 8192:
                raise ValueError("ID token missing")
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            raise ServiceError("UNAUTHENTICATED", "OIDC code exchange failed", 401) from error
    finally:
        if owned_client:
            client.close()
    claims = _verified_id_claims(id_token, config, nonce)
    require_tenant_membership(db, tenant_id, claims["sub"])
    expiry = min(
        datetime.fromtimestamp(int(claims["exp"]), UTC),
        now + timedelta(hours=SESSION_HOURS),
    )
    if expiry <= now:
        raise ServiceError("UNAUTHENTICATED", "OIDC session has expired", 401)
    token = secrets.token_urlsafe(48)
    session = BrowserSession(
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        tenant_id=tenant_id, subject=claims["sub"], created_at=now, expires_at=expiry,
    )
    db.add(session)
    return token, session


def csrf_for(token: str, config: Settings) -> str:
    return hmac.new(
        config.browser_session_secret.encode(), f"csrf:{token}".encode(), hashlib.sha256
    ).hexdigest()


def browser_identity(
    db: Session, token: str | None, config: Settings, *,
    method: str = "GET", csrf: str | None = None, now: datetime | None = None,
) -> tuple[str, str]:
    if not token or len(token) > 128:
        raise ServiceError("UNAUTHENTICATED", "Browser session is missing", 401)
    session = db.get(BrowserSession, hashlib.sha256(token.encode()).hexdigest())
    now = now or utcnow()
    if session is None:
        raise ServiceError("UNAUTHENTICATED", "Browser session is invalid", 401)
    expiry = session.expires_at.replace(tzinfo=UTC) if session.expires_at.tzinfo is None \
        else session.expires_at
    if expiry <= now:
        raise ServiceError("UNAUTHENTICATED", "Browser session has expired", 401)
    if method not in {"GET", "HEAD", "OPTIONS"} and (
        not csrf or not hmac.compare_digest(csrf, csrf_for(token, config))
    ):
        raise ServiceError("CSRF_DENIED", "Browser request token is invalid", 403)
    require_tenant_membership(db, session.tenant_id, session.subject)
    return session.tenant_id, session.subject
