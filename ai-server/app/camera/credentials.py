"""
Camera credential handling - adapted from the original project's
utils/camera_manager.py. Passwords are never stored in the client-readable
`cameras` table; they live in the `camera_secrets` table, which has no RLS
policies and is only reachable with the service-role key held by this local
process.

Two storage modes, identical to the original project's scheme:

  1. "env:VAR_NAME" (recommended) - resolved from an environment
     variable only at connect time. Nothing secret is ever persisted.
  2. "obf:..." - a locally-obfuscated (XOR + base64) token, using a key
     file generated on first run (data/.camera_key, gitignored).
     This is obfuscation, not strong encryption - same caveat as the
     original project.
"""
import base64
import os
import secrets
from pathlib import Path

from app.core.config import settings

ENV_PREFIX = "env:"
OBF_PREFIX = "obf:"

# Kept in ai-server/data/ (gitignored) - it is purely local state and must
# never be committed.
_KEY_FILE = settings.data_dir / ".camera_key"


def _get_or_create_key() -> bytes:
    if _KEY_FILE.exists():
        try:
            return _KEY_FILE.read_bytes()
        except Exception:
            pass
    key = secrets.token_bytes(32)
    try:
        _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _KEY_FILE.write_bytes(key)
        try:
            os.chmod(_KEY_FILE, 0o600)
        except Exception:
            pass
    except Exception:
        pass
    return key


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def obfuscate_secret(plain: str) -> str:
    if not plain:
        return ""
    key = _get_or_create_key()
    token = base64.urlsafe_b64encode(_xor(plain.encode("utf-8"), key)).decode("ascii")
    return f"{OBF_PREFIX}{token}"


def _deobfuscate_secret(token: str) -> str:
    key = _get_or_create_key()
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    return _xor(raw, key).decode("utf-8", errors="replace")


def resolve_credential(stored_value: str) -> str:
    if not stored_value:
        return ""
    if stored_value.startswith(ENV_PREFIX):
        return os.environ.get(stored_value[len(ENV_PREFIX):], "")
    if stored_value.startswith(OBF_PREFIX):
        try:
            return _deobfuscate_secret(stored_value[len(OBF_PREFIX):])
        except Exception:
            return ""
    return stored_value


def credential_is_set(stored_value: str) -> bool:
    return bool(stored_value)
