import uuid
from decimal import Decimal

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.metrics import VALIDATION_REJECTIONS


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        requested=None,
        available=None,
        parent_total=None,
        details: dict | None = None,
    ):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.requested, self.available, self.parent_total = requested, available, parent_total
        self.details = details


def _with_rows(details: dict | None, rows: list | None) -> dict | None:
    if rows is not None:
        return {**(details or {}), "rows": rows}
    return details


def _numeric(code: str, message: str, requested, available, parent_total, rows=None) -> ApiError:
    return ApiError(422, code, message, requested, available, parent_total, _with_rows(None, rows))


def parent_overallocated(requested, available, parent_total, rows=None) -> ApiError:
    return _numeric(
        "PARENT_OVERALLOCATED",
        "Allocation exceeds the parent's available amount",
        requested,
        available,
        parent_total,
        rows,
    )


def pmo_pool_insufficient(requested, available, parent_total, rows=None) -> ApiError:
    return _numeric(
        "PMO_POOL_INSUFFICIENT",
        "PMO pool is insufficient",
        requested,
        available,
        parent_total,
        rows,
    )


def share_exceeds_grant(requested, available, parent_total, rows=None) -> ApiError:
    return _numeric(
        "SHARE_EXCEEDS_GRANT", "Shares exceed the grant", requested, available, parent_total, rows
    )


def grant_below_shared(requested, available, parent_total, rows=None) -> ApiError:
    return _numeric(
        "GRANT_BELOW_SHARED", "Grant is below the amount shared", requested, available,
        parent_total, rows,
    )  # fmt: skip


def share_expiry_after_grant(rows=None) -> ApiError:
    return ApiError(
        422, "SHARE_EXPIRY_AFTER_GRANT", "Share expires after its grant",
        details=_with_rows(None, rows),
    )  # fmt: skip


def limit_out_of_policy(field: str, value, min, max, rows=None) -> ApiError:
    return ApiError(
        422, "LIMIT_OUT_OF_POLICY", f"{field} is outside the allowed policy range",
        requested=value, details=_with_rows({"field": field, "min": min, "max": max}, rows),
    )  # fmt: skip


def self_allocation_forbidden() -> ApiError:
    return ApiError(422, "SELF_ALLOCATION_FORBIDDEN", "You cannot allocate to yourself")


def scope_forbidden(message: str = "Target is outside your scope", rows=None) -> ApiError:
    return ApiError(422, "SCOPE_FORBIDDEN", message, details=_with_rows(None, rows))


def member_state_conflict(status: str) -> ApiError:
    return ApiError(
        409, "MEMBER_STATE_CONFLICT", "The action does not fit the employee's state",
        details={"status": status},
    )  # fmt: skip


def lifecycle_in_progress(pending: int, failed: int) -> ApiError:
    return ApiError(
        409, "LIFECYCLE_IN_PROGRESS", "Earlier changes for this employee are not applied yet",
        details={"pending": pending, "failed": failed},
    )  # fmt: skip


def invalid_request(message: str, details: dict | None = None) -> ApiError:
    return ApiError(422, "INVALID_REQUEST", message, details=details)


_HTTP_CODES = {
    401: ("UNAUTHENTICATED", "Authentication required"),
    403: ("FORBIDDEN", "Forbidden"),
    404: ("NOT_FOUND", "Not found"),
}


def _request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if rid is None:
        rid = request.state.request_id = str(uuid.uuid4())
    return rid


def _envelope(request: Request, status: int, code: str, message: str, **extra) -> JSONResponse:
    rid = _request_id(request)
    body = {"code": code, "message": message}
    body.update({k: v for k, v in extra.items() if v is not None})
    body["request_id"] = rid
    return JSONResponse(
        jsonable_encoder(body, custom_encoder={Decimal: float}),
        status_code=status,
        headers={"X-Request-ID": rid},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        if exc.status == 422:
            VALIDATION_REJECTIONS.labels(exc.code).inc()
        return _envelope(
            request, exc.status, exc.code, exc.message, requested=exc.requested,
            available=exc.available, parent_total=exc.parent_total, details=exc.details,
        )  # fmt: skip

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return _envelope(
            request, 422, "INVALID_REQUEST", "Request validation failed", details={"errors": errors}
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code, message = _HTTP_CODES.get(exc.status_code, ("INVALID_REQUEST", str(exc.detail)))
        if exc.status_code == 403 and exc.detail == "CSRF_FAILED":
            code, message = "CSRF_FAILED", "CSRF token mismatch"
        return _envelope(request, exc.status_code, code, message)
