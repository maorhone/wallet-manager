"""SPEC §9.2: both ADFS tokens on the onboarding endpoints are validated strictly."""

import pytest
from helpers import jwt as t
from joserfc import jwt as jose
from joserfc.jwk import KeySet, RSAKey

from app.auth import portal_jwt
from app.main import create_app

UPN = "dani@corp.example"


async def get(portal, headers):
    return await portal.client.get("/onboarding", headers=headers)


def tokens(portal, portal_kw=None, user_kw=None, upn=UPN):
    return {
        "Authorization": f"Bearer {t.portal_token(portal.secret, **(portal_kw or {}))}",
        "X-User-Assertion": t.user_token(portal.secret, upn, **(user_kw or {})),
    }


async def test_valid_pair_accepted(portal, provisioned):
    assert (await get(portal, tokens(portal))).status_code == 200


async def test_missing_portal_token_401(portal):
    h = tokens(portal)
    del h["Authorization"]
    r = await get(portal, h)
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"


async def test_missing_user_assertion_401(portal):
    h = tokens(portal)
    del h["X-User-Assertion"]
    assert (await get(portal, h)).status_code == 401


async def test_client_not_allowlisted_403(portal):
    r = await get(portal, tokens(portal, {"client_id": "someone-else"}))
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"


async def test_portal_token_with_upn_rejected(portal):
    assert (await get(portal, tokens(portal, {"upn": UPN}))).status_code == 401


async def test_user_wrong_audience_401(portal):
    assert (await get(portal, tokens(portal, user_kw={"aud": "other-app"}))).status_code == 401
    assert (await get(portal, tokens(portal, {"aud": "other-api"}))).status_code == 401


async def test_expired_401(portal):
    assert (await get(portal, tokens(portal, user_kw={"exp_in": -3600}))).status_code == 401
    assert (await get(portal, tokens(portal, {"exp_in": -3600}))).status_code == 401


async def test_user_without_upn_or_wrong_issuer_401(portal):
    claims = t.user_claims(UPN)
    del claims["upn"]
    h = {**tokens(portal), "X-User-Assertion": t.sign_hs256(claims, portal.secret)}
    assert (await get(portal, h)).status_code == 401
    bad = t.user_claims(UPN, iss="https://evil.example")
    h["X-User-Assertion"] = t.sign_hs256(bad, portal.secret)
    assert (await get(portal, h)).status_code == 401


async def test_wrong_signature_401(portal):
    h = {**tokens(portal), "X-User-Assertion": t.user_token("not-the-secret-0123456789abcdef", UPN)}
    assert (await get(portal, h)).status_code == 401


async def test_hs256_rejected_without_dev_auth(make_portal):
    portal = await make_portal(dev_auth=False, dev_jwt_secret="")
    assert (await get(portal, tokens(portal))).status_code == 401


async def test_dev_secret_without_dev_auth_refuses_to_start(settings):
    bad = settings.model_copy(update={"dev_auth": False, "dev_jwt_secret": "x" * 32})
    with pytest.raises(RuntimeError, match="DEV_JWT_SECRET"):
        create_app(bad)


async def test_alg_none_rejected(portal):
    h = {
        "Authorization": f"Bearer {t.alg_none(t.portal_claims())}",
        "X-User-Assertion": t.alg_none(t.user_claims(UPN)),
    }
    assert (await get(portal, h)).status_code == 401
    assert (
        await get(portal, {**tokens(portal), "X-User-Assertion": h["X-User-Assertion"]})
    ).status_code == 401


async def test_session_cookie_ignored(portal):
    login = await portal.client.post("/auth/dev/login", json={"upn": "dana@corp.example"})
    assert login.status_code == 200 and portal.client.cookies.get("wm_session")
    assert (await portal.client.get("/onboarding")).status_code == 401
    r = await portal.client.post("/onboarding", json={}, headers={"Idempotency-Key": "k"})
    assert r.status_code == 401  # not a CSRF 403: the path is exempt, and still needs the tokens


async def test_rs256_via_jwks(make_portal, provisioned, monkeypatch):
    portal = await make_portal(dev_auth=False, dev_jwt_secret="", adfs_jwks_url="https://x.example")
    key = RSAKey.generate_key(2048, parameters={"kid": "k1"})

    async def fake_jwks(settings):
        return KeySet([key])

    monkeypatch.setattr(portal_jwt, "load_jwks", fake_jwks)

    def sign(claims):
        return jose.encode({"alg": "RS256", "kid": "k1"}, claims, key)

    h = {
        "Authorization": f"Bearer {sign(t.portal_claims())}",
        "X-User-Assertion": sign(t.user_claims(UPN)),
    }
    assert (await get(portal, h)).status_code == 200
    h["X-User-Assertion"] = jose.encode(
        {"alg": "RS256", "kid": "k1"}, t.user_claims(UPN, aud="x"), key
    )
    assert (await get(portal, h)).status_code == 401
