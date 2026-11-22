"""
NURI · 누리 — cloud/crypto.py
Cifrado de los datos que se publican en GitHub (el repositorio es público, tus picks no).

    clave = PBKDF2-HMAC-SHA256(código NURI, sal aleatoria, 200 000 vueltas) → 32 bytes
    datos = AES-256-GCM(clave, nonce aleatorio de 12 bytes)

La app del celular hace exactamente lo mismo con WebCrypto, así que sólo quien tiene el código
puede leer los picks y el historial.
"""
from __future__ import annotations

import base64
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITER = 200_000


def _key(code: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER)
    return kdf.derive(code.strip().upper().encode("utf-8"))


def encrypt_bytes(raw: bytes, code: str) -> dict:
    salt, iv = os.urandom(16), os.urandom(12)
    ct = AESGCM(_key(code, salt)).encrypt(iv, raw, None)
    b = lambda x: base64.b64encode(x).decode("ascii")  # noqa: E731
    return {"v": 1, "kdf": "pbkdf2-sha256", "iter": ITER, "salt": b(salt), "iv": b(iv), "ct": b(ct)}


def decrypt_bytes(blob: dict, code: str) -> bytes:
    d = lambda x: base64.b64decode(x)  # noqa: E731
    return AESGCM(_key(code, d(blob["salt"]))).decrypt(d(blob["iv"]), d(blob["ct"]), None)


def encrypt_json(obj, code: str) -> str:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    return json.dumps(encrypt_bytes(raw, code))
