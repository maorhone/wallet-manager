"""Call POST /onboarding as the portal would (DEV_AUTH only).

Usage: ``uv run python scripts/dev_onboard.py upn``.

Signs HS256 tokens with DEV_JWT_SECRET, using the first ONBOARDING_CLIENT_IDS and
ONBOARDING_USER_AUDIENCES entries, and prints the response. ``--get`` reads the status instead.
"""

import argparse
import json
import secrets
import sys
import time

import httpx
from joserfc import jwt
from joserfc.jwk import OctKey

from app.config import Settings


def token(key: OctKey, **claims) -> str:
    now = int(time.time())
    return jwt.encode({"alg": "HS256"}, {"nbf": now - 5, "exp": now + 600, **claims}, key)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("upn")
    parser.add_argument("--api", default="http://127.0.0.1:4101")
    parser.add_argument("--get", action="store_true")
    args = parser.parse_args()
    s = Settings()
    if not (s.dev_auth and s.dev_jwt_secret and s.onboarding_client_ids):
        print("needs DEV_AUTH=1, DEV_JWT_SECRET and ONBOARDING_CLIENT_IDS", file=sys.stderr)
        return 1
    key = OctKey.import_key(s.dev_jwt_secret)
    headers = {
        "Authorization": "Bearer "
        + token(
            key, iss=s.adfs_issuer, aud=s.onboarding_audience, appid=s.onboarding_client_ids[0]
        ),
        "X-User-Assertion": token(
            key, iss=s.adfs_issuer, aud=s.onboarding_user_audiences[0], upn=args.upn
        ),
    }
    if args.get:
        r = httpx.get(f"{args.api}/onboarding", headers=headers)
    else:
        headers["Idempotency-Key"] = secrets.token_hex(8)
        r = httpx.post(f"{args.api}/onboarding", headers=headers, json={})
    print(r.status_code)
    print(json.dumps(r.json(), indent=2))
    return 0 if r.is_success else 1


if __name__ == "__main__":
    sys.exit(main())
