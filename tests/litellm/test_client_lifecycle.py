import json

import httpx
import pytest
from pydantic import ValidationError

from app.litellm_client import (
    JwtKeyMappingDelete,
    JwtKeyMappingUpdate,
    KeyDelete,
    LiteLLMClient,
    UserDelete,
)

IDENT = dict(jwt_issuer="https://adfs.test", jwt_claim_name="upn", jwt_claim_value="u@x.test")


async def test_typed_bodies_for_new_endpoints():
    seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"status": "success"})

    client = LiteLLMClient("http://fake", "k", transport=httpx.MockTransport(handler))
    await client.key_delete(KeyDelete(keys=["abc"]))
    await client.user_delete(UserDelete(user_ids=["u@x.test"]))
    await client.jwt_mapping_update(JwtKeyMappingUpdate(**IDENT, is_active=False))
    await client.jwt_mapping_delete(JwtKeyMappingDelete(**IDENT))
    await client.aclose()
    assert seen == [
        ("POST", "/key/delete", {"keys": ["abc"]}),
        ("POST", "/user/delete", {"user_ids": ["u@x.test"]}),
        ("POST", "/jwt/key/mapping/update", {**IDENT, "is_active": False}),
        ("POST", "/jwt/key/mapping/delete", IDENT),
    ]
    with pytest.raises(ValidationError):
        KeyDelete(keys=["a"], extra=1)
