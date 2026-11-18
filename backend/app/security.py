"""Password hashing (stdlib scrypt) and opaque session tokens."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_N, _R, _P, _DKLEN = 2**14, 8, 1, 64
MIN_PASSWORD_LEN = 10


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return "scrypt${}${}${}${}${}".format(_N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(dk_b64)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt_b64),
                            n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False


def password_problems(password: str) -> list[str]:
    problems = []
    if len(password) < MIN_PASSWORD_LEN:
        problems.append(f"كلمة المرور لازم تكون {MIN_PASSWORD_LEN} حروف على الأقل")
    if password.isdigit() or password.isalpha():
        problems.append("كلمة المرور لازم تجمع بين حروف وأرقام")
    return problems


def totp_new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret_b32: str, counter: int) -> str:
    import struct
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    return f"{(int.from_bytes(digest[off:off + 4], 'big') & 0x7FFFFFFF) % 1_000_000:06d}"


def totp_verify(secret_b32: str, code: str, last_counter: int | None = None, at: float | None = None) -> int | None:
    """Returns the matched time-step counter (±1 step window) or None. Rejects a counter already used (replay)."""
    import time
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != 6:
        return None
    now = int((at if at is not None else time.time()) // 30)
    for c in (now - 1, now, now + 1):
        if hmac.compare_digest(totp_code(secret_b32, c), code) and (last_counter is None or c > last_counter):
            return c
    return None


def totp_uri(secret_b32: str, account: str, issuer: str = "IT Administration") -> str:
    from urllib.parse import quote
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret_b32}&issuer={quote(issuer)}&digits=6&period=30"


def new_session_token() -> tuple[str, str]:
    """Returns (token for the cookie, sha256 hex stored in DB)."""
    token = secrets.token_urlsafe(32)
    return token, token_digest(token)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
