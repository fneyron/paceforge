"""Deterministic deployed-code identity, also used to invalidate browser assets."""

import hashlib
from pathlib import Path


def fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".html", ".css", ".js", ".json"}:
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


BUILD_ID = fingerprint(Path(__file__).resolve().parent)
