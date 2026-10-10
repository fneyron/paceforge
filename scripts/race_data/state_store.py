"""Persist the research corpus on a dedicated branch, never on application main.

Only the three explicitly listed paths are restored/saved. No force-push and
no credentials, app code, caches or fitted Python objects enter the snapshot.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path

PATHS = ("data/races", "scripts/race_data/utmb_tenants.json", "reports/race-data")
BRANCH = "race-data"


def git(*args, cwd=None):
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def fetch():
    exists = git("ls-remote", "--heads", "origin", f"refs/heads/{BRANCH}")
    if not exists:
        return None
    git(
        "fetch",
        "--no-tags",
        "--depth=1",
        "origin",
        f"refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}",
    )
    return git("rev-parse", f"origin/{BRANCH}")


def restore():
    sha = fetch()
    if sha:
        paths = [path for path in PATHS if git("ls-tree", "--name-only", sha, "--", path)]
        archive = subprocess.check_output(["git", "archive", sha, "--", *paths])
        with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
            stream.extractall(filter="data")
    return sha


def save():
    root = Path(git("rev-parse", "--show-toplevel"))
    sha = fetch()
    branch = "race-data-bootstrap-" + uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix="paceforge-race-store-") as temporary:
        worktree = Path(temporary) / "snapshot"
        git("worktree", "add", "--detach", str(worktree), sha or "HEAD")
        try:
            if sha is None:
                git("switch", "--orphan", branch, cwd=worktree)
            for relative in PATHS:
                source, target = root / relative, worktree / relative
                if not source.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.is_dir():
                    if target.exists():
                        shutil.rmtree(target)
                    shutil.copytree(
                        source,
                        target,
                        ignore=shutil.ignore_patterns("*.partial.json", ".refresh-*"),
                    )
                else:
                    shutil.copy2(source, target)
                git("add", "--", relative, cwd=worktree)
            changed = git("diff", "--cached", "--name-only", cwd=worktree)
            if changed:
                git(
                    "-c",
                    "user.name=github-actions[bot]",
                    "-c",
                    "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                    "commit",
                    "-m",
                    "Update race corpus and validation reports",
                    cwd=worktree,
                )
                git("push", "origin", f"HEAD:refs/heads/{BRANCH}", cwd=worktree)
            return git("rev-parse", "HEAD", cwd=worktree)
        finally:
            git("worktree", "remove", "--force", str(worktree))
            if sha is None:
                git("branch", "-D", branch)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("restore", "save"))
    args = parser.parse_args()
    sha = restore() if args.action == "restore" else save()
    print(json.dumps({"branch": BRANCH, "commit": sha, "bootstrap": sha is None}))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write(f"snapshot={sha or ''}\n")


if __name__ == "__main__":
    main()
