"""JWT authentication for REST endpoints.

Fetches the RSA public key from Identity Service's JWKS endpoint,
caches it, and validates Bearer tokens on every request.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import jwt
import structlog
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from submission_service import config

logger = structlog.get_logger(__name__)

_security = HTTPBearer()

# Cached JWKS public key
_jwks_key: dict | None = None


@dataclass
class UserContext:
    user_id: str
    email: str
    role: str
    name: str


async def _fetch_jwks_key() -> dict:
    """Fetch the RSA public key from Identity Service JWKS endpoint. Cached after first call."""
    global _jwks_key
    if _jwks_key is not None:
        return _jwks_key

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(config.IDENTITY_JWKS_URL, timeout=10)
            resp.raise_for_status()
            jwks = resp.json()

        key_data = jwks["keys"][0]
        _jwks_key = key_data
        logger.info("jwks_fetched", kid=key_data.get("kid"))
        return _jwks_key
    except Exception as e:
        logger.error("jwks_fetch_failed", error=str(e))
        raise HTTPException(status_code=503, detail="Unable to validate authentication")


def _build_public_key(jwk: dict):
    """Build an RSA public key from JWK parameters."""
    from jwt.algorithms import RSAAlgorithm

    return RSAAlgorithm.from_jwk(jwk)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(_security),
) -> UserContext:
    """FastAPI dependency — validates JWT and returns user context.

    Usage:
        @router.post("/assessments")
        async def create_assessment(user: UserContext = Depends(get_current_user)):
            ...
    """
    token = credentials.credentials

    try:
        jwk = await _fetch_jwks_key()
        public_key = _build_public_key(jwk)

        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=config.JWT_ISSUER,
            audience=config.JWT_AUDIENCE,
        )

        return UserContext(
            user_id=payload["sub"],
            email=payload.get("email", ""),
            role=payload.get("role", ""),
            name=payload.get("name", ""),
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        logger.warn("jwt_invalid", error=str(e))
        raise HTTPException(status_code=401, detail="Invalid token")
