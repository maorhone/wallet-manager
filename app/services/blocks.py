"""Classify failed LiteLLM requests into user-facing block reasons (SoT D-060–D-064).

A 429 or a timeout is never assumed to be a budget problem: only an explicit budget error is.
"""

from collections import Counter

CREDITS_EXHAUSTED = "CREDITS_EXHAUSTED"
RATE_LIMIT = "RATE_LIMIT"
MODEL_CONGESTION = "MODEL_CONGESTION"
ACCESS_DENIED = "ACCESS_DENIED"
SERVICE_ERROR = "SERVICE_ERROR"
UNKNOWN = "UNKNOWN"

# reason -> next-best-action code (the UI maps codes to Hebrew text)
ACTIONS = {
    CREDITS_EXHAUSTED: "REQUEST_CREDITS",
    RATE_LIMIT: "REQUEST_THROUGHPUT",
    MODEL_CONGESTION: "RETRY_LATER_OR_OTHER_MODEL",
    ACCESS_DENIED: "REQUEST_ACCESS",
    SERVICE_ERROR: "CONTACT_AI_TEAM",
    UNKNOWN: "CONTACT_AI_TEAM",
}


def classify(
    status: int | None, exception_type: str = "", message: str = ""
) -> tuple[str, str | None]:
    """Return (reason, sub); sub is rpm|tpm|concurrency for RATE_LIMIT, else None."""
    text = f"{exception_type} {message}".lower()
    if "budget" in text and "exceed" in text:
        return CREDITS_EXHAUSTED, None
    if "parallel" in text or "concurren" in text:
        return RATE_LIMIT, "concurrency"
    if status == 429 and ("tpm" in text or "tokens per minute" in text):
        return RATE_LIMIT, "tpm"
    if status == 429 and ("rpm" in text or "requests per minute" in text or "rate limit" in text):
        return RATE_LIMIT, "rpm" if "tpm" not in text else "tpm"
    if status in (429, 503) and any(w in text for w in ("overload", "congest", "capacity")):
        return MODEL_CONGESTION, None
    if status in (401, 403) or "model_not_allowed" in text:
        return ACCESS_DENIED, None
    if status is not None and 500 <= status <= 599:
        return SERVICE_ERROR, None
    return UNKNOWN, None


def describe(row) -> dict:
    reason, sub = classify(row.status_code, row.exception_type, row.message)
    return {
        "at": row.at,
        "model": row.model,
        "reason": reason,
        "sub": sub,
        "retry_after": row.retry_after,
        "action": ACTIONS[reason],
    }


def aggregate(rows) -> list[dict]:
    """Counts by (reason, sub) and consumer; no request content."""
    by_reason = Counter()
    by_consumer = Counter()
    for r in rows:
        reason, sub = classify(r.status_code, r.exception_type, r.message)
        by_reason[(reason, sub)] += 1
        by_consumer[(reason, r.user)] += 1
    return [
        {"reason": k[0], "sub": k[1], "count": n, "action": ACTIONS[k[0]]}
        for k, n in sorted(by_reason.items(), key=lambda i: (-i[1], i[0][0]))
    ], [
        {"reason": k[0], "consumer": k[1], "count": n}
        for k, n in sorted(by_consumer.items(), key=lambda i: (i[0][0], str(i[0][1])))
    ]
