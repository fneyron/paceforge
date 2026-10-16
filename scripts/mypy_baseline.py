"""mypy held to « no new finding »: today's findings against mypy-baseline.txt (docs/quality.md).

    python scripts/mypy_baseline.py check     fails on a finding the baseline does not hold
    python scripts/mypy_baseline.py update    rewrites the baseline from today's findings

The baseline holds one line per finding without its line number (code moves): « path: error: message  [code] ».
mypy reads its configuration from pyproject.toml ([tool.mypy]).
"""
import collections
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASELINE = ROOT / "mypy-baseline.txt"
FINDING = re.compile(r"^(?P<path>[^:\n]+):(?P<line>\d+)(?::\d+)?: error: (?P<message>.*)$")


def findings() -> list[tuple[str, int, str]]:
    """(path, line, message) for each error mypy reports."""
    run = subprocess.run([sys.executable, "-m", "mypy"], cwd=ROOT, capture_output=True, text=True)
    if run.returncode not in (0, 1):  # mypy itself failed (a syntax error, a bad configuration)
        sys.stdout.write(run.stdout)
        sys.stderr.write(run.stderr)
        sys.exit(run.returncode)
    out = []
    for line in run.stdout.splitlines():
        m = FINDING.match(line)
        if m:
            out.append((m["path"], int(m["line"]), m["message"]))
    return out


def main(command: str) -> int:
    found = findings()
    current = collections.Counter(f"{path}: error: {message}" for path, _, message in found)
    if command == "update":
        BASELINE.write_text("".join(f"{line}\n" for line in sorted(current.elements())), encoding="utf-8")
        print(f"{sum(current.values())} finding(s) written to {BASELINE.name}")
        return 0
    if command != "check":
        print(__doc__)
        return 2
    held = BASELINE.read_text(encoding="utf-8").splitlines() if BASELINE.exists() else []
    baseline = collections.Counter(held)
    new, gone = current - baseline, baseline - current
    if gone:
        print(f"{sum(gone.values())} baseline finding(s) no longer reported: `update` tightens the baseline")
    if not new:
        print(f"mypy: no new finding ({sum(current.values())} held by {BASELINE.name})")
        return 0
    shown: collections.Counter[str] = collections.Counter()
    for path, line, message in found:  # each new finding with its line, annotated on the commit in Actions
        key = f"{path}: error: {message}"
        if shown[key] < new[key]:
            shown[key] += 1
            print(f"{path}:{line}: error: {message}")
            if os.environ.get("GITHUB_ACTIONS"):
                print(f"::error file={path},line={line}::mypy: {message}")
    print(f"mypy: {sum(new.values())} new finding(s), not in {BASELINE.name}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "check"))
