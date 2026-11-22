#!/usr/bin/env python3
"""Watch a deployment from outside: ask /health every second until it serves the expected build, and measure how
long the site did not answer in between.

Coolify's compose build pack stops every container of the application before it starts the new ones
(docs/ops-runbook.md, « Déployer »): each deployment has a window where the proxy answers 502 or 503. The app
shell and the service worker repeat a page request through that window (RETRY_BUDGET_S in mobile/src/retry.ts,
BUDGET in app/static/sw.js); this script fails the deploy job when the window outgrows what they cover, or when
the build never arrives. Standard library only: the job runs it on a bare checkout.

    python scripts/deploy/watch.py --url https://paceforge.fr/health --expect <build id> --max-outage 60
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


@dataclass
class Outage:
    """A stretch of answers that were not a 200 from the app, from its first one to the next 200."""
    start: float
    end: float
    answers: dict[str, int] = field(default_factory=dict)

    @property
    def seconds(self) -> float:
        return self.end - self.start


def outages(samples: list[tuple[float, str]]) -> list[Outage]:
    """The outages in `samples`, (time, answer) pairs in order, where the answer is "200" or what was got instead
    (a status, "timeout", "unreachable"). An outage runs from its first failed answer to the first 200 after it; one
    still open at the last sample ends there."""
    found: list[Outage] = []
    current: Outage | None = None
    for at, answer in samples:
        if answer == "200":
            if current:
                current.end = at
                found.append(current)
                current = None
            continue
        if current is None:
            current = Outage(at, at)
        current.end = at
        current.answers[answer] = current.answers.get(answer, 0) + 1
    if current:
        found.append(current)
    return found


def ask(url: str, timeout: float = 5.0) -> tuple[str, str]:
    """(answer, build): "200" and the build /health reports, or what came instead and ""."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Cache-Control": "no-cache"}),
                                    timeout=timeout) as response:
            return "200", str(json.load(response).get("build", ""))
    except urllib.error.HTTPError as error:
        return str(error.code), ""
    except (TimeoutError, urllib.error.URLError) as error:
        timed_out = isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError)
        return ("timeout" if timed_out else "unreachable"), ""
    except (ValueError, OSError):
        return "unreachable", ""  # a 200 that is not the app's JSON, a connection cut while reading


def watch(url: str, expect: str, limit: float, every: float = 1.0, settle: int = 3, ask=ask, clock=time.monotonic,
          sleep=time.sleep, say=print) -> tuple[bool, list[tuple[float, str]]]:
    """Ask `url` every `every` seconds until `settle` answers in a row carry the build `expect` (True) or `limit`
    seconds have passed (False), with every (seconds since the start, answer) on the way."""
    samples: list[tuple[float, str]] = []
    start, last, in_a_row = clock(), None, 0
    while (now := clock() - start) < limit:
        answer, build = ask(url)
        samples.append((now, answer))
        seen = f"{answer} build {build[:12]}" if answer == "200" else answer
        if seen != last:
            say(f"{now:6.0f} s  {seen}")
            last = seen
        in_a_row = in_a_row + 1 if build == expect else 0
        if in_a_row >= settle:
            return True, samples
        sleep(max(0.0, every - (clock() - start - now)))
    return False, samples


def report(served: bool, found: list[Outage], expect: str, max_outage: float) -> tuple[bool, str]:
    """(passed, the summary in Markdown)."""
    longest = max((o.seconds for o in found), default=0.0)
    lines = [f"### Deployment of build `{expect[:12]}`", ""]
    if not found:
        lines.append("No failed answer from `/health` while the containers were replaced.")
    for outage in found:
        answers = ", ".join(f"{count} × {answer}" for answer, count in sorted(outage.answers.items()))
        lines.append(f"- {outage.seconds:.0f} s without an answer from the app, from {outage.start:.0f} s "
                     f"to {outage.end:.0f} s after the trigger ({answers})")
    passed = served and longest <= max_outage
    if not served:
        lines += ["", "**The site never served this build**: see Coolify's deployment log (docs/ops-runbook.md)."]
    elif longest > max_outage:
        lines += ["", f"**{longest:.0f} s is more than the {max_outage:.0f} s the app and the service worker "
                      "cover by repeating the request**: a page has shown an error (docs/ops-runbook.md, « Déployer »)."]
    else:
        lines += ["", f"Longest: {longest:.0f} s, within the {max_outage:.0f} s covered by the automatic retry."]
    return passed, "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--expect", required=True, help="the build id /health must end up serving")
    parser.add_argument("--max-outage", type=float, default=60.0, help="seconds of failed answers tolerated in a row")
    parser.add_argument("--limit", type=float, default=600.0, help="seconds to wait for the build")
    args = parser.parse_args(argv)
    served, samples = watch(args.url, args.expect, args.limit)
    passed, summary = report(served, outages(samples), args.expect, args.max_outage)
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
            out.write(summary)
    if not passed:
        print("::error::" + ("the site did not serve the build" if not served else "the deployment's outage is "
                              "longer than the automatic retry covers") + " (docs/ops-runbook.md)")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
