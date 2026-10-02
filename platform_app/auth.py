"""Signed bearer identity and database-backed tenant/project authorization."""

from __future__ import annotations

from functools import lru_cache

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError
from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_app.config import Settings, settings
from platform_app.models import Project, ProjectMembership, Tenant, TenantMembership
from platform_app.service import ServiceError

READ_ROLES = frozenset({"maintainer", "contributor", "reviewer", "viewer"})
WRITE_ROLES = frozenset({"maintainer", "contributor"})
REVIEW_ROLES = frozenset({"maintainer", "reviewer"})


@lru_cache(maxsize=8)
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, cache_jwk_set=True, lifespan=300, timeout=5)


def verify_bearer(token: str, config: Settings | None = None) -> str:
    """Return only the verified subject. The caller resolves tenant membership in SQL."""
    config = config or settings()
    if not token or len(token) > 8192:
        raise ServiceError("UNAUTHENTICATED", "Invalid bearer token", 401)
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise ValueError("Unsupported JWT header")
        key = _jwks_client(config.oidc_jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, key.key, algorithms=["RS256"], audience=config.oidc_audience,
            issuer=config.oidc_issuer, leeway=30,
            options={"require": ["iss", "sub", "aud", "exp", "iat"]},
        )
        subject = claims["sub"]
        if not isinstance(subject, str) or not 1 <= len(subject) <= 200:
            raise ValueError("Invalid subject")
        return subject
    except (PyJWTError, ValueError, KeyError) as error:
        raise ServiceError("UNAUTHENTICATED", "Invalid bearer token", 401) from error


def require_tenant_membership(db: Session, tenant_id: str, subject: str) -> TenantMembership:
    tenant = db.get(Tenant, tenant_id)
    membership = db.scalar(select(TenantMembership).where(
        TenantMembership.tenant_id == tenant_id,
        TenantMembership.subject == subject,
        TenantMembership.status == "active",
    ))
    if tenant is None or tenant.status != "active" or membership is None:
        raise ServiceError("NOT_FOUND", "Workspace not found", 404)
    return membership


def is_owner(db: Session, identity: tuple[str, str]) -> bool:
    if settings().environment == "development":
        return True
    return require_tenant_membership(db, identity[0], identity[1]).role == "owner"


def require_owner(db: Session, identity: tuple[str, str]) -> None:
    if not is_owner(db, identity):
        raise ServiceError("TOOL_DENIED", "Organization owner role is required", 403)


def require_project_role(
    db: Session, identity: tuple[str, str], project_id: str,
    roles: frozenset[str] = READ_ROLES,
) -> Project:
    if settings().environment != "development":
        require_tenant_membership(db, identity[0], identity[1])
    project = db.scalar(select(Project).where(
        Project.id == project_id, Project.tenant_id == identity[0]
    ))
    if project is None:
        raise ServiceError("NOT_FOUND", "Project not found", 404)
    if settings().environment == "development" or (
        roles == READ_ROLES and is_owner(db, identity)
    ):
        return project
    membership = db.scalar(select(ProjectMembership).where(
        ProjectMembership.tenant_id == identity[0],
        ProjectMembership.project_id == project_id,
        ProjectMembership.subject == identity[1],
        ProjectMembership.status == "active",
    ))
    if membership is None or membership.role not in roles:
        raise ServiceError("NOT_FOUND", "Project not found", 404)
    return project


def visible_project_ids(db: Session, identity: tuple[str, str]) -> set[str] | None:
    """None means tenant owner or local development can read all tenant projects."""
    if settings().environment != "development":
        require_tenant_membership(db, identity[0], identity[1])
    if settings().environment == "development" or is_owner(db, identity):
        return None
    return set(db.scalars(select(ProjectMembership.project_id).where(
        ProjectMembership.tenant_id == identity[0],
        ProjectMembership.subject == identity[1],
        ProjectMembership.status == "active",
        ProjectMembership.role.in_(READ_ROLES),
    )).all())
