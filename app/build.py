"""Deterministic deployed-code identity, also used to invalidate browser assets."""

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# What a deploy ships. The smoke test (deploy.yml) waits until /health reports the fingerprint of the pushed
# tree, and the Dockerfile copies the same files so the container computes the same value. The Dockerfile and
# the compose file are left out: Coolify rewrites them at build time (it adds ARG lines), so the container could
# never report the tree's value. A deploy that changes only those two files is therefore not told apart by the
# smoke test; the Coolify deployment log is the check then.
DEPLOYED = ("app", "alembic", "alembic.ini", "pyproject.toml",
            "requirements.lock", "requirements-dev.lock", "requirements-research.lock")
SUFFIXES = {".py", ".html", ".css", ".js", ".json", ".gz", ".ini", ".toml", ".lock"}


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


# The image minifies its stylesheet (Dockerfile): its build id is computed before, on the files as committed, and kept
# in this file, read here, so the container still reports the checked-out tree's fingerprint. No suffix: it never
# counts in a fingerprint. Git and the Docker context ignore it: only the image's own build writes it.
FROZEN = ROOT / "app" / "BUILD_ID"


def frozen(path: Path = FROZEN) -> str | None:
    """The build id kept at `path` when it holds one, else None (a tree, not an image)."""
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value if re.fullmatch(r"[0-9a-f]{64}", value) else None


BUILD_ID = frozen() or fingerprint(ROOT, DEPLOYED)

if __name__ == "__main__":  # the Dockerfile: python -m app.build > app/BUILD_ID, before anything is minified
    print(fingerprint(ROOT, DEPLOYED))
