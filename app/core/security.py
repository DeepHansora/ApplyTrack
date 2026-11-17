"""Security Core Module for ApplyTrack.

Provides password hashing via Argon2id, timing-attack-resistant dummy
verification, cryptographically secure session token generation, and
SHA-256 session token hashing.
"""

import hashlib
import secrets

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

# Configure Argon2id PasswordHasher with approved parameters:
# - type=Type.ID (Argon2id hybrid mode, resistant to GPU and side-channel attacks)
# - time_cost=2 (2 iterations)
# - memory_cost=65536 (64 MB RAM per hash, making GPU brute-force attacks expensive)
# - parallelism=1 (1 thread per hash)
_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=65536,
    parallelism=1,
    type=Type.ID,
)

# Genuinely precomputed Argon2id hash constant generated once using the exact
# parameters above (type=Type.ID, time_cost=2, memory_cost=65536, parallelism=1).
# Stored as a static module-level constant so no hashing calculation occurs
# during module import or during dummy verification.
_DUMMY_HASH: str = (
    "$argon2id$v=19$m=65536,t=2,p=1$c65ySk58HPQ8ZrL7fy8ysw$XYYB6MqGr1qIyQfQujGxJAuWB/067l53cfj6o+HkOLY"
)


def hash_password(password: str) -> str:
    """Hash a plain-text password using Argon2id.

    Args:
        password: The plain-text password string to hash.

    Returns:
        The encoded Argon2id password hash string.

    Raises:
        ValueError: If password is empty or not a string.
    """
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string.")

    return _hasher.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain-text password against a stored Argon2id hash.

    Args:
        plain_password: The plain-text password to verify.
        hashed_password: The stored Argon2id hash string.

    Returns:
        True if the password matches the hash, False otherwise.
        Safely returns False on malformed, corrupted, or non-matching hashes.
    """
    if not isinstance(plain_password, str) or not plain_password:
        return False

    if not isinstance(hashed_password, str) or not hashed_password:
        return False

    try:
        return _hasher.verify(hashed_password, plain_password)
    except (VerificationError, InvalidHashError, ValueError):
        return False


def dummy_verify_password(password: str) -> bool:
    """Execute dummy password verification against a precomputed Argon2id hash.

    Reduces timing differences between recognized and unrecognized email accounts
    during login attempts, making timing-based user enumeration significantly more
    difficult.

    Note on Timing Guarantees:
        While dummy verification executes the full memory-hard Argon2id verification
        algorithm to bridge the large latency gap (spending ~80-100ms instead of ~1ms),
        it reduces timing differences rather than guaranteeing strictly identical
        response times. Variance in network latency, database query execution, and CPU
        scheduling may still introduce subtle timing fluctuations.

    Args:
        password: The supplied password to verify against the precomputed dummy hash.

    Returns:
        Always False.
    """
    if isinstance(password, str) and password:
        try:
            _hasher.verify(_DUMMY_HASH, password)
        except (VerificationError, InvalidHashError, ValueError):
            pass

    return False


def generate_session_token() -> str:
    """Generate a cryptographically secure random session token.

    Uses secrets.token_urlsafe(32) to generate 32 random bytes (256 bits of
    entropy) represented as a URL-safe Base64 string.

    Returns:
        A high-entropy raw session token string.
    """
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """Hash a raw session token using SHA-256 for secure database storage.

    The raw token is transmitted to the user's browser in an HttpOnly cookie,
    while only its SHA-256 digest is stored in the database. If the database
    is compromised, attackers cannot reconstruct valid session cookies.

    Args:
        token: The raw session token string.

    Returns:
        A 64-character hexadecimal SHA-256 digest string.

    Raises:
        ValueError: If token is empty or not a string.
    """
    if not isinstance(token, str) or not token:
        raise ValueError("Session token must be a non-empty string.")

    return hashlib.sha256(token.encode("utf-8")).hexdigest()
