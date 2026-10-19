from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Request
from joserfc.errors import JoseError
from starlette.responses import RedirectResponse, Response

from app.auth.session import start_session
from app.config import Settings
from app.errors import ApiError

router = APIRouter(prefix="/auth")


def build_oauth(settings: Settings) -> OAuth:
    oauth = OAuth()
    oauth.register(
        "adfs",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        server_metadata_url=settings.oidc_discovery_url,
        client_kwargs={"scope": "openid profile", "code_challenge_method": "S256"},
    )
    return oauth


def _client(request: Request):
    if not request.app.state.settings.oidc_discovery_url:
        raise ApiError(404, "NOT_FOUND", "OIDC is not configured")
    return request.app.state.oauth.adfs


@router.get("/login")
async def login(request: Request):
    return await _client(request).authorize_redirect(
        request, request.app.state.settings.oidc_redirect_uri
    )


@router.get("/callback")
async def callback(request: Request):
    settings = request.app.state.settings
    try:
        token = await _client(request).authorize_access_token(request)
    except (OAuthError, JoseError) as exc:  # bad state, nonce, signature, expired token...
        raise ApiError(401, "UNAUTHENTICATED", "Sign-in failed") from exc
    claims = token["userinfo"]  # verified id_token claims; ADFS tokens are not kept (§12.10)
    upn = claims.get(settings.oidc_upn_claim)
    if not upn:
        raise ApiError(401, "UNAUTHENTICATED", "Token has no UPN claim")
    groups = claims.get(settings.oidc_groups_claim) or []
    if isinstance(groups, str):
        groups = [groups]
    response = RedirectResponse("/", status_code=303)
    await start_session(
        request, response, upn, claims.get(settings.oidc_name_claim) or upn, list(groups)
    )
    return response


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    response = Response(status_code=204)
    response.delete_cookie("wm_csrf")
    return response
