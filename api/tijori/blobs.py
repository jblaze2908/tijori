"""Raw upload store: content-addressed files under TIJORI_BLOB_DIR, one directory per member.

At rest each file is sealed with SecretBox (the vault's envelope encryption: a fresh data key per file,
wrapped by the master key, bound to the member and the file's plaintext SHA-256). Files written before
encryption existed are sealed in place by `encrypt_existing`, which the worker runs at start. Reads
accept both, so there is no flag day. Directories 0700, files 0600, owned by the app user.
"""

import base64
import hashlib
import json
import os
from pathlib import Path

from tijori.secretbox import SecretBox, SecretError, Sealed


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


MAGIC = b"TJB1\n"


def _seal(box: "SecretBox", member_id: int, digest: str, data: bytes) -> bytes:
    sealed = box.seal(member_id, f"blob:{digest}", data)
    head = {"v": sealed.key_version, "wk": _b64(sealed.wrapped_key), "kn": _b64(sealed.key_nonce), "n": _b64(sealed.nonce)}
    return MAGIC + json.dumps(head).encode() + b"\n" + sealed.ciphertext


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(payload)
    os.replace(tmp, path)


def store_blob(root: Path, member_id: int, data: bytes, box: "SecretBox | None" = None) -> tuple[str, str]:
    """Write once; returns (sha256 of the plaintext, blob_ref relative to root). Idempotent by content."""
    digest = sha256_hex(data)
    rel = Path(f"m{member_id}") / digest[:2] / digest
    path = root / rel
    if not path.exists():
        _write(path, _seal(box, member_id, digest, data) if box else data)
    return digest, str(rel)


def read_blob(root: Path, member_id: int, ref: str, box: "SecretBox | None") -> bytes:
    """The plaintext of a stored file, sealed or (from before encryption) plain. One file read."""
    path = (root / ref).resolve()
    if root.resolve() not in path.parents:
        raise FileNotFoundError(ref)
    raw = path.read_bytes()
    if not raw.startswith(MAGIC):
        return raw
    if box is None:
        raise SecretError("blob is sealed but no master key is configured")
    head_end = raw.index(b"\n", len(MAGIC))
    h = json.loads(raw[len(MAGIC):head_end])
    sealed = Sealed(h["v"], _unb64(h["wk"]), _unb64(h["kn"]), _unb64(h["n"]), raw[head_end + 1:])
    return box.open(member_id, f"blob:{path.name}", sealed)


def encrypt_existing(root: Path, member_id: int, box: "SecretBox") -> int:
    """Seal every plain file under the member's directory, in place. Returns how many were sealed."""
    n = 0
    base = root / f"m{member_id}"
    if not base.exists():
        return 0
    for path in base.glob("*/*"):
        if path.suffix == ".part" or not path.is_file():
            continue
        with path.open("rb") as f:
            if f.read(len(MAGIC)) == MAGIC:
                continue
        data = path.read_bytes()
        if sha256_hex(data) != path.name:  # not one of ours; leave it alone
            continue
        _write(path, _seal(box, member_id, path.name, data))
        n += 1
    return n


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)
