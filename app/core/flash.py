"""Flash message utilities using HMAC-SHA256 signed client-side cookies.

Provides temporary one-time operational alerts (success, error, warning, info)
across HTTP redirects (Post/Redirect/Get) without requiring database persistence.
Messages are signed to prevent client-side tampering and are automatically
consumed and deleted upon display.
"""

import base64
import hashlib
import hmac
import json
from typing import Literal

from starlette.requests import Request
from starlette.responses import Response

from app.config import settings

FlashCategory = Literal["success", "error", "warning", "info"]
ALLOWED_FLASH_CATEGORIES = {"success", "error", "warning", "info"}
MAX_FLASH_MESSAGE_LENGTH = 500
MAX_FLASH_COOKIE_LENGTH = 4096


def encode_flash_message(
    message: str,
    category: str = "info",
    secret_key: str | None = None,
) -> str:
    """Serialize and sign a flash message dictionary.

    Args:
        message: The alert message text (max 500 characters).
        category: The category ('success', 'error', 'warning', 'info').
        secret_key: Optional secret key override (defaults to settings.SECRET_KEY).

    Returns:
        A signed string formatted as '{base64_payload}.{hmac_sha256_signature}'.

    Raises:
        ValueError: If category is invalid, message is empty, or exceeds length limit.
    """
    if category not in ALLOWED_FLASH_CATEGORIES:
        raise ValueError(
            f"Invalid flash message category '{category}'. Must be one of: {sorted(ALLOWED_FLASH_CATEGORIES)}"
        )

    if not isinstance(message, str) or not message.strip():
        raise ValueError("Flash message must be a non-empty string.")

    cleaned_message = message.strip()
    if len(cleaned_message) > MAX_FLASH_MESSAGE_LENGTH:
        raise ValueError(
            f"Flash message exceeds maximum length of {MAX_FLASH_MESSAGE_LENGTH} characters."
        )

    payload = {"category": category, "message": cleaned_message}
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(payload_bytes).decode("utf-8")

    key = (secret_key or settings.SECRET_KEY).encode("utf-8")
    signature = hmac.new(key, b64_payload.encode("utf-8"), hashlib.sha256).hexdigest()

    return f"{b64_payload}.{signature}"


def decode_flash_message(
    cookie_value: str | None,
    secret_key: str | None = None,
) -> dict[str, str] | None:
    """Validate and deserialize a signed flash message cookie.

    Verifies the HMAC-SHA256 signature using constant-time comparison.
    If the signature is invalid, tampered with, or the payload is malformed,
    returns None.

    Args:
        cookie_value: The cookie string formatted as '{base64_payload}.{signature}'.
        secret_key: Optional secret key override.

    Returns:
        A dict with 'category' and 'message' keys if valid, otherwise None.
    """
    if not isinstance(cookie_value, str) or len(cookie_value) > MAX_FLASH_COOKIE_LENGTH or "." not in cookie_value:
        return None

    parts = cookie_value.split(".", 1)
    if len(parts) != 2:
        return None

    b64_payload, signature = parts
    if not b64_payload or not signature:
        return None

    key = (secret_key or settings.SECRET_KEY).encode("utf-8")
    expected_signature = hmac.new(key, b64_payload.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(signature, expected_signature):
        return None

    try:
        payload_bytes = base64.urlsafe_b64decode(b64_payload.encode("utf-8"))
        data = json.loads(payload_bytes.decode("utf-8"))

        if not isinstance(data, dict):
            return None

        category = data.get("category")
        message = data.get("message")

        if category not in ALLOWED_FLASH_CATEGORIES or not isinstance(message, str):
            return None

        if not message or len(message) > MAX_FLASH_MESSAGE_LENGTH:
            return None

        return {"category": category, "message": message}
    except Exception:
        return None


def set_flash_message(
    response: Response,
    message: str,
    category: str = "info",
) -> None:
    """Attach a signed flash message cookie to an outgoing response.

    Args:
        response: The FastAPI/Starlette Response or RedirectResponse object.
        message: The message string to display to the user.
        category: The category ('success', 'error', 'warning', 'info').
    """
    signed_value = encode_flash_message(message=message, category=category)
    response.set_cookie(
        key=settings.FLASH_COOKIE_NAME,
        value=signed_value,
        max_age=settings.FLASH_COOKIE_MAX_AGE,
        path=settings.FLASH_COOKIE_PATH,
        domain=None,
        secure=settings.FLASH_COOKIE_SECURE,
        httponly=settings.FLASH_COOKIE_HTTPONLY,
        samesite=settings.FLASH_COOKIE_SAMESITE,
    )


def get_flash_message(
    request: Request,
    response: Response | None = None,
) -> dict[str, str] | None:
    """Retrieve and consume a flash message from the request.

    If a Response is provided, deletes the flash cookie from the response
    to guarantee the message is displayed only once. Also caches the result
    on request.state.flash_message.

    Args:
        request: The incoming Starlette Request.
        response: Optional Response on which to schedule flash cookie deletion.

    Returns:
        The flash message dict {'category': ..., 'message': ...} if present and valid,
        or None.
    """
    if hasattr(request.state, "flash_message"):
        msg = request.state.flash_message
        if response is not None and request.cookies.get(settings.FLASH_COOKIE_NAME):
            response.delete_cookie(
                key=settings.FLASH_COOKIE_NAME,
                path=settings.FLASH_COOKIE_PATH,
                domain=None,
            )
        return msg

    raw_cookie = request.cookies.get(settings.FLASH_COOKIE_NAME)
    if not raw_cookie:
        request.state.flash_message = None
        return None

    request.state.flash_cookie_cleared = True

    if response is not None:
        response.delete_cookie(
            key=settings.FLASH_COOKIE_NAME,
            path=settings.FLASH_COOKIE_PATH,
            domain=None,
        )

    message = decode_flash_message(raw_cookie)
    request.state.flash_message = message
    return message
