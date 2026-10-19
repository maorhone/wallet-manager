import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://adfs.test/adfs"
CLIENT_ID = "wm-client"
GROUPS = [
    "CN=wm-head-a1,OU=Groups,DC=corp,DC=example",
    "CN=unrelated,OU=Groups,DC=corp,DC=example",
]


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
async def oidc(make_client):
    client = await make_client(
        oidc_discovery_url=f"{ISSUER}/.well-known/openid-configuration",
        oidc_client_id=CLIENT_ID,
        oidc_client_secret="s3cret",
        oidc_redirect_uri="http://test/auth/callback",
    )
    return client


def _mock_adfs(router: respx.MockRouter, key, nonce_for) -> None:
    router.get(f"{ISSUER}/.well-known/openid-configuration").respond(
        json={
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "jwks_uri": f"{ISSUER}/keys",
        }
    )
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    router.get(f"{ISSUER}/keys").respond(json={"keys": [{**jwk, "kid": "k1", "use": "sig"}]})

    def token(request: httpx.Request) -> httpx.Response:
        now = int(time.time())
        id_token = jwt.encode(
            {
                "iss": ISSUER, "aud": CLIENT_ID, "iat": now, "exp": now + 300,
                "nonce": nonce_for(), "sub": "adfs-sub-1",
                "upn": "dana@corp.example", "name": "Dana Levi",
                "groups": GROUPS,
            },
            key, algorithm="RS256", headers={"kid": "k1"},
        )  # fmt: skip
        return httpx.Response(
            200, json={"access_token": "adfs-at", "token_type": "Bearer", "id_token": id_token}
        )

    router.post(f"{ISSUER}/token").mock(side_effect=token)


async def _start(client) -> tuple[str, str, str]:
    r = await client.get("/auth/login")
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and "code_challenge" in q
    return q["state"][0], q["nonce"][0], r.headers["location"]


async def test_callback_creates_session_with_filtered_groups(oidc, key):
    with respx.mock(assert_all_called=False) as router:
        nonce_box = []
        _mock_adfs(router, key, lambda: nonce_box[0])
        state, nonce, _ = await _start(oidc)
        nonce_box.append(nonce)
        r = await oidc.get("/auth/callback", params={"code": "abc", "state": state})
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "wm_csrf" in oidc.cookies

    me = (await oidc.get("/me")).json()
    assert me["upn"] == "dana@corp.example" and me["display_name"] == "Dana Levi"
    assert [r["role"] for r in me["roles"]] == ["employee", "group_head"]

    # Only DNs present in role_binding are kept; no ADFS token reaches the browser.
    import base64

    from itsdangerous import TimestampSigner

    raw = TimestampSigner("change-me").unsign(oidc.cookies["wm_session"].encode())
    session = json.loads(base64.b64decode(raw))
    assert session["groups"] == GROUPS[:1]
    assert "adfs-at" not in oidc.cookies["wm_session"] and "id_token" not in session


async def test_callback_rejects_bad_nonce(oidc, key):
    with respx.mock(assert_all_called=False) as router:
        _mock_adfs(router, key, lambda: "not-the-nonce")
        state, _, _ = await _start(oidc)
        r = await oidc.get("/auth/callback", params={"code": "abc", "state": state})
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"
    assert (await oidc.get("/me")).status_code == 401
