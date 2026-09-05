from __future__ import annotations


def is_cloudflare_1015(exc: BaseException) -> bool:
    text = (getattr(exc, "text", None) or str(exc) or "").lower()
    return "error 1015" in text or ("cloudflare" in text and "rate limited" in text)


def retry_after_from_exception(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None
