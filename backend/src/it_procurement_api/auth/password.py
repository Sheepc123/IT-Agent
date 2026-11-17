"""Password hashing and opaque session-token digests for local development."""
import hashlib
import hmac
import secrets


def hash_password(password: str) -> str:
    """Create a salted scrypt hash for a local application password."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Check a password against the stored hash without timing-sensitive comparison."""
    try:
        algorithm, n, r, p, salt, digest = encoded.split("$")
        if (algorithm, n, r, p) != ("scrypt", "16384", "8", "1"):
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


def token_digest(token: str) -> str:
    """Store and look up only a digest of each random session token."""
    return hashlib.sha256(token.encode()).hexdigest()
