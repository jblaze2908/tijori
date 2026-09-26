"""SecretBox: envelope encryption for member secrets (app passwords, statement passwords).

Each secret gets a fresh 256-bit data key; the secret is sealed with AES-256-GCM under that key
(96-bit random nonce, AAD = "<member_id>:<name>"), and the data key is wrapped with the master
key (AES-GCM, its own nonce, the same AAD with a prefix). Rows keep key_version, wrapped key,
both nonces and the ciphertext; rotating TIJORI_MASTER_KEY re-wraps data keys only.
"""

import base64
import binascii
import hashlib
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32
NONCE_BYTES = 12


class SecretError(RuntimeError):
    """Wrong key, tampered row, or a row bound to another member/name. Never carries plaintext."""


@dataclass(frozen=True, slots=True)
class Sealed:
    key_version: str
    wrapped_key: bytes
    key_nonce: bytes
    nonce: bytes
    ciphertext: bytes


def parse_master_key(value: str) -> bytes:
    """Base64 of exactly 32 bytes; raises ValueError without echoing the value."""
    try:
        key = base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("master key must be base64") from None
    if len(key) != KEY_BYTES:
        raise ValueError("master key must decode to 32 bytes")
    return key


def key_version(key: bytes) -> str:
    """A short public fingerprint naming which master key wrapped a row."""
    return hashlib.sha256(b"tijori-master-key|" + key).hexdigest()[:16]


def _aad(member_id: int, name: str) -> bytes:
    return f"{member_id}:{name}".encode()


class SecretBox:
    def __init__(self, master_key: bytes, old_key: bytes | None = None) -> None:
        self.current = key_version(master_key)
        self._keys = {self.current: master_key}
        if old_key is not None:
            self._keys.setdefault(key_version(old_key), old_key)

    def seal(self, member_id: int, name: str, plaintext: bytes) -> Sealed:
        aad = _aad(member_id, name)
        data_key = AESGCM.generate_key(bit_length=256)
        nonce, key_nonce = os.urandom(NONCE_BYTES), os.urandom(NONCE_BYTES)
        ciphertext = AESGCM(data_key).encrypt(nonce, plaintext, aad)
        wrapped = AESGCM(self._keys[self.current]).encrypt(key_nonce, data_key, b"dek|" + aad)
        return Sealed(self.current, wrapped, key_nonce, nonce, ciphertext)

    def _data_key(self, member_id: int, name: str, sealed: Sealed) -> bytes:
        master = self._keys.get(sealed.key_version)
        if master is None:
            raise SecretError("secret was sealed with a master key that is not configured")
        try:
            return AESGCM(master).decrypt(sealed.key_nonce, sealed.wrapped_key, b"dek|" + _aad(member_id, name))
        except InvalidTag:
            raise SecretError("secret failed authentication") from None

    def open(self, member_id: int, name: str, sealed: Sealed) -> bytes:
        data_key = self._data_key(member_id, name, sealed)
        try:
            return AESGCM(data_key).decrypt(sealed.nonce, sealed.ciphertext, _aad(member_id, name))
        except InvalidTag:
            raise SecretError("secret failed authentication") from None

    def rewrap(self, member_id: int, name: str, sealed: Sealed) -> Sealed:
        """Re-wrap the data key under the current master key; the ciphertext is untouched."""
        if sealed.key_version == self.current:
            return sealed
        data_key = self._data_key(member_id, name, sealed)
        key_nonce = os.urandom(NONCE_BYTES)
        wrapped = AESGCM(self._keys[self.current]).encrypt(key_nonce, data_key, b"dek|" + _aad(member_id, name))
        return Sealed(self.current, wrapped, key_nonce, sealed.nonce, sealed.ciphertext)
