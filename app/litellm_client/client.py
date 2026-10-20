"""The only place that speaks HTTP to the LiteLLM management API (NFR3).

Nothing here logs: the master key and returned ``key`` values must never reach logs (NFR5).
"""

from typing import TypeVar

import httpx
from pydantic import BaseModel

from .errors import LiteLLMPermanentError, LiteLLMTransientError
from .models import (
    JwtKeyMappingDelete,
    JwtKeyMappingNew,
    JwtKeyMappingRecord,
    JwtKeyMappingUpdate,
    KeyBlock,
    KeyDelete,
    KeyGenerate,
    KeyGenerateResponse,
    KeyListResponse,
    KeyUpdate,
    OrganizationInfo,
    OrganizationMemberAdd,
    OrganizationNew,
    OrganizationUpdate,
    TeamInfo,
    TeamMemberAdd,
    TeamMemberDelete,
    TeamMemberUpdate,
    TeamNew,
    TeamRecord,
    TeamUpdate,
    UserDelete,
    UserNew,
    UserRecord,
)

T = TypeVar("T", bound=BaseModel)


class LiteLLMClient:
    def __init__(
        self,
        base_url: str,
        master_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ):
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {master_key}"},
            transport=transport,
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(
        self,
        method: str,
        path: str,
        out: type[T] | None,
        body: BaseModel | None = None,
        params: dict | None = None,
    ) -> T | None:
        try:
            resp = await self._http.request(
                method, path, json=body.payload() if body else None, params=params
            )
        except httpx.TransportError as exc:  # connect errors, timeouts, protocol errors
            raise LiteLLMTransientError(f"{method} {path}: {type(exc).__name__}") from None
        if resp.status_code >= 500:
            raise LiteLLMTransientError(f"{method} {path}: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text
            raise LiteLLMPermanentError(resp.status_code, detail)
        return out.model_validate(resp.json()) if out else None

    # --- health ---
    async def health(self) -> bool:
        await self._call("GET", "/health/liveliness", None)
        return True

    # --- organizations ---
    async def organization_new(self, body: OrganizationNew) -> OrganizationInfo:
        return await self._call("POST", "/organization/new", OrganizationInfo, body)

    async def organization_update(self, body: OrganizationUpdate) -> OrganizationInfo:
        return await self._call("PATCH", "/organization/update", OrganizationInfo, body)

    async def organization_member_add(self, body: OrganizationMemberAdd) -> OrganizationInfo:
        return await self._call("POST", "/organization/member_add", OrganizationInfo, body)

    async def organization_info(self, organization_id: str) -> OrganizationInfo:
        return await self._call(
            "GET",
            "/organization/info",
            OrganizationInfo,
            params={"organization_id": organization_id},
        )

    # --- teams ---
    async def team_new(self, body: TeamNew) -> TeamRecord:
        return await self._call("POST", "/team/new", TeamRecord, body)

    async def team_update(self, body: TeamUpdate) -> TeamRecord:
        return await self._call("POST", "/team/update", TeamRecord, body)

    async def team_info(self, team_id: str) -> TeamInfo:
        return await self._call("GET", "/team/info", TeamInfo, params={"team_id": team_id})

    async def team_member_add(self, body: TeamMemberAdd) -> TeamInfo:
        return await self._call("POST", "/team/member_add", TeamInfo, body)

    async def team_member_update(self, body: TeamMemberUpdate) -> TeamInfo:
        return await self._call("POST", "/team/member_update", TeamInfo, body)

    async def team_member_delete(self, body: TeamMemberDelete) -> TeamInfo:
        return await self._call("POST", "/team/member_delete", TeamInfo, body)

    # --- users and keys ---
    async def user_new(self, body: UserNew) -> UserRecord:
        return await self._call("POST", "/user/new", UserRecord, body)

    async def key_generate(self, body: KeyGenerate) -> KeyGenerateResponse:
        return await self._call("POST", "/key/generate", KeyGenerateResponse, body)

    async def key_update(self, body: KeyUpdate) -> None:
        await self._call("POST", "/key/update", None, body)

    async def key_block(self, body: KeyBlock) -> None:
        await self._call("POST", "/key/block", None, body)

    async def key_unblock(self, body: KeyBlock) -> None:
        await self._call("POST", "/key/unblock", None, body)

    async def key_delete(self, body: KeyDelete) -> None:
        await self._call("POST", "/key/delete", None, body)

    async def user_delete(self, body: UserDelete) -> None:
        await self._call("POST", "/user/delete", None, body)

    async def key_list(self, user_id: str) -> KeyListResponse:
        return await self._call("GET", "/key/list", KeyListResponse, params={"user_id": user_id})

    # --- SSO mapping ---
    async def jwt_mapping_new(self, body: JwtKeyMappingNew) -> JwtKeyMappingRecord:
        return await self._call("POST", "/jwt/key/mapping/new", JwtKeyMappingRecord, body)

    async def jwt_mapping_update(self, body: JwtKeyMappingUpdate) -> None:
        await self._call("POST", "/jwt/key/mapping/update", None, body)

    async def jwt_mapping_delete(self, body: JwtKeyMappingDelete) -> None:
        await self._call("POST", "/jwt/key/mapping/delete", None, body)
