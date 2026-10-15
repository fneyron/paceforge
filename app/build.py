"""Deterministic deployed-code identity, also used to invalidate browser assets."""

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# What a deploy ships, and what shapes its image. The smoke test (deploy.yml) waits until /health reports the
# fingerprint of the pushed tree: every file whose change calls for a new container must count, and the
# Dockerfile copies the same files so the container computes the same value.
DEPLOYED = ("app", "alembic", "alembic.ini", "Dockerfile", "docker-compose.prod.yml", "pyproject.toml",
            "requirements.lock", "requirements-dev.lock", "requirements-research.lock")
SUFFIXES = {".py", ".html", ".css", ".js", ".json", ".gz", ".ini", ".yml", ".toml", ".lock"}


def fingerprint(root: Path, parts: tuple[str, ...] = ("",)) -> str:
    """Hash of the paths and contents of the files under each of root's `parts` (a directory or a file):
    bytecode, missing parts and checkout times never count."""
    digest = hashlib.sha256()
    for part in parts:
        base = root / part
        paths = sorted(p for p in base.rglob("*") if p.is_file()) if base.is_dir() else [base]
        for path in paths:
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.suffix not in SUFFIXES and path.name != "Dockerfile":
                continue
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


BUILD_ID = fingerprint(ROOT, DEPLOYED)
