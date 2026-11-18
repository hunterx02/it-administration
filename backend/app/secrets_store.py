"""Encrypted secrets store (AES-256-GCM).

- Key: ITADM_SECRET_KEY (base64, 32 bytes) from backend/.env — never in Git, never in the DB.
- Data: a JSON file OUTSIDE the project folder (default %LOCALAPPDATA%/ITAdministration/secrets.enc.json).
- Values are write-only through the API: names can be listed, values are never returned to the browser.
- Each value is bound to its name (AEAD associated data), so swapping ciphertexts between names fails.
"""
from __future__ import annotations

import base64
import json
import os
import secrets as _s
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretStoreError(Exception):
    pass


def _key(b64: str) -> bytes:
    try:
        k = base64.b64decode(b64)
    except Exception as e:  # noqa: BLE001
        raise SecretStoreError("مفتاح التشفير غير صالح") from e
    if len(k) != 32:
        raise SecretStoreError("مفتاح التشفير لازم يكون 32 بايت (AES-256)")
    return k


def encrypt(key_b64: str, plaintext: str, context: str) -> str:
    nonce = _s.token_bytes(12)
    ct = AESGCM(_key(key_b64)).encrypt(nonce, plaintext.encode("utf-8"), context.encode("utf-8"))
    return base64.b64encode(nonce + ct).decode()


def decrypt(key_b64: str, token: str, context: str) -> str:
    raw = base64.b64decode(token)
    try:
        return AESGCM(_key(key_b64)).decrypt(raw[:12], raw[12:], context.encode("utf-8")).decode("utf-8")
    except Exception as e:  # noqa: BLE001
        raise SecretStoreError("تعذر فك التشفير — المفتاح اتغير أو الملف اتعدّل") from e


class SecretStore:
    def __init__(self, path: Path, key_b64: str):
        self.path, self.key = Path(path), key_b64

    def _load(self) -> dict:
        if not self.path.is_file():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def names(self) -> list[dict]:
        return [{"name": k, "updated_at": v.get("updated_at")} for k, v in sorted(self._load().items())]

    def set(self, name: str, value: str) -> None:
        data = self._load()
        data[name] = {"v": encrypt(self.key, value, "secret:" + name),
                      "updated_at": datetime.now(timezone.utc).isoformat()}
        self._save(data)

    def get(self, name: str) -> str | None:
        item = self._load().get(name)
        return None if item is None else decrypt(self.key, item["v"], "secret:" + name)

    def delete(self, name: str) -> bool:
        data = self._load()
        if name not in data:
            return False
        del data[name]
        self._save(data)
        return True


def store_for(settings) -> SecretStore:  # noqa: ANN001
    if not settings.secret_key or not settings.secrets_path:
        raise SecretStoreError("مخزن الأسرار غير مُعد")
    return SecretStore(settings.secrets_path, settings.secret_key)
