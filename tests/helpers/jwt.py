"""Dummy ADFS-style JWTs for the onboarding endpoints (SPEC §9.2). Secrets are generated per run."""

import base64
import json
import time

from joserfc import jwt
from joserfc.jwk import OctKey

ISSUER = "https://adfs.corp.example/adfs"
PORTAL_AUD = "wm-api-test"
USER_AUD = "onboarding-portal-test"
CLIENT_ID = "portal-client-test"


def sign_hs256(claims: dict, secret: str) -> str:
    return jwt.encode({"alg": "HS256"}, claims, OctKey.import_key(secret))


def _times(exp_in: int) -> dict:
    now = int(time.time())
    return {"iat": now, "nbf": now - 5, "exp": now + exp_in}


def portal_claims(client_id: str = CLIENT_ID, aud: str = PORTAL_AUD, exp_in: int = 600, **extra):
    return {"iss": ISSUER, "aud": aud, "appid": client_id, **_times(exp_in), **extra}


def user_claims(upn: str, aud: str = USER_AUD, exp_in: int = 600, **extra):
    return {"iss": ISSUER, "aud": aud, "upn": upn, **_times(exp_in), **extra}


def portal_token(secret: str, **kw) -> str:
    return sign_hs256(portal_claims(**kw), secret)


def user_token(secret: str, upn: str, **kw) -> str:
    return sign_hs256(user_claims(upn, **kw), secret)


def alg_none(claims: dict) -> str:
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none', 'typ': 'JWT'})}.{part(claims)}."
