"""Cheap content-change detection hash, shared between the scan pipeline
(which computes it once per track at scan time) and the backup module
(which needs to recompute it on demand for a track whose stored value
is unavailable — see changes/applier.py's use of this at apply time).
"""

from __future__ import annotations

from hashlib import blake2b
from pathlib import Path

_HASH_CHUNK = 64 * 1024


def partial_content_hash(path: Path, size_bytes: int) -> str:
    """blake2b over the first/last 64KB + size.

    Deliberately not a full-file hash: hashing every byte of a 50k-file
    library on every rescan would dominate scan time. This catches tag
    edits and truncation/corruption without reading the whole file.
    """
    hasher = blake2b()
    with path.open("rb") as fh:
        head = fh.read(_HASH_CHUNK)
        hasher.update(head)
        if size_bytes > _HASH_CHUNK:
            fh.seek(max(size_bytes - _HASH_CHUNK, len(head)))
            hasher.update(fh.read(_HASH_CHUNK))
    hasher.update(str(size_bytes).encode())
    return hasher.hexdigest()
