"""Onboarding portal authentication (SPEC §9.2): the portal's token plus the user's ADFS JWT.

RS256 against the ADFS JWKS; HS256 with ``DEV_JWT_SECRET`` only when ``DEV_AUTH=1``. Tokens are
never logged and session cookies are never read here.
"""

import base64
import json
import time
from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import Depends, Request
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet, OctKey

from app.config import Settings
from app.errors import ApiError

LEEWAY_S = 60
_JWKS_TTL_S = 300
_jwks_cache: dict[str, tuple[float, KeySet]] = {}


def _unauthenticated() -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", "Invalid or missing token")


async def load_jwks(settings: Settings) -> KeySet:
    """The ADFS signing keys, cached; the URL comes from settings or the OIDC discovery document."""
    async with httpx.AsyncClient(timeout=5) as http:
        try:
            url = settings.adfs_jwks_url
            if not url and settings.oidc_discovery_url:
                url = (await http.get(settings.oidc_discovery_url)).json()["jwks_uri"]
            if not url:
                raise _unauthenticated()
            cached = _jwks_cache.get(url)
            if cached and time.monotonic() - cached[0] < _JWKS_TTL_S:
                return cached[1]
            keys = KeySet.import_key_set((await http.get(url)).raise_for_status().json())
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise ApiError(503, "NOT_READY", "Token signing keys are unavailable") from exc
    _jwks_cache[url] = (time.monotonic(), keys)
    return keys


def _alg(token: str) -> str:
    try:
        head = token.split(".")[0]
        return json.loads(base64.urlsafe_b64decode(head + "=" * (-len(head) % 4)))["alg"]
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise _unauthenticated() from exc


async def _claims(settings: Settings, token: str, audiences: list[str]) -> dict:
    """Verify signature (strict algorithm list), issuer, audience, exp and nbf."""
    alg = _alg(token)
    if alg == "HS256" and settings.dev_auth and settings.dev_jwt_secret:
        key = OctKey.import_key(settings.dev_jwt_secret)
    elif alg == "RS256":
        key = await load_jwks(settings)
    else:
        raise _unauthenticated()
    if not settings.adfs_issuer or not audiences:
        raise _unauthenticated()  # not configured: fail closed
    try:
        decoded = jwt.decode(token, key, algorithms=[alg])
        jwt.JWTClaimsRegistry(
            leeway=LEEWAY_S,
            iss={"essential": True, "value": settings.adfs_issuer},
            aud={"essential": True, "values": audiences},
            exp={"essential": True},
            nbf={},
        ).validate(decoded.claims)
    except (JoseError, ValueError, TypeError) as exc:
        raise _unauthenticated() from exc
    return decoded.claims


async def verify_portal(settings: Settings, token: str) -> str:
    """The portal's client-credentials token; returns its client id. Must not carry ``upn``."""
    claims = await _claims(settings, token, [a for a in [settings.onboarding_audience] if a])
    if "upn" in claims:
        raise _unauthenticated()
    client_id = next((claims[c] for c in ("appid", "client_id", "azp") if claims.get(c)), None)
    if not isinstance(client_id, str):
        raise _unauthenticated()
    if client_id not in settings.onboarding_client_ids:
        raise ApiError(403, "FORBIDDEN", "This client may not call the onboarding API")
    return client_id


async def verify_user(settings: Settings, token: str) -> str:
    """The user's ADFS JWT; returns the upn, which the body can never override."""
    claims = await _claims(settings, token, settings.onboarding_user_audiences)
    upn = claims.get("upn")
    if not isinstance(upn, str) or not upn:
        raise _unauthenticated()
    return upn


@dataclass(frozen=True)
class PortalCaller:
    client_id: str
    upn: str


async def portal_auth(request: Request) -> PortalCaller:
    settings: Settings = request.app.state.settings
    if not settings.onboarding_user_audiences:
        raise ApiError(404, "NOT_FOUND", "Onboarding is not enabled")
    auth = request.headers.get("Authorization", "")
    assertion = request.headers.get("X-User-Assertion", "")
    scheme, _, bearer = auth.partition(" ")
    if scheme.lower() != "bearer" or not bearer or not assertion:
        raise _unauthenticated()
    client_id = await verify_portal(settings, bearer.strip())
    return PortalCaller(client_id, await verify_user(settings, assertion.strip()))


PortalAuth = Annotated[PortalCaller, Depends(portal_auth)]
