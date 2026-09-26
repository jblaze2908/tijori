"""Raw upload store: content-addressed files under TIJORI_BLOB_DIR, one directory per member.

TODO(M1): envelope encryption at rest (PLAN §10). Until then blobs rely on the volume's
permissions: directories 0700, files 0600, owned by the api user.
"""

import hashlib
import os
from pathlib import Path


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def store_blob(root: Path, member_id: int, data: bytes) -> tuple[str, str]:
    """Write once; returns (sha256, blob_ref relative to root). Idempotent by content."""
    digest = sha256_hex(data)
    rel = Path(f"m{member_id}") / digest[:2] / digest
    path = root / rel
    if not path.exists():
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    return digest, str(rel)
