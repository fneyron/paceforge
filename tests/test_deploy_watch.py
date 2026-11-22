"""The deployment as users meet it: the watcher of the deploy job (scripts/deploy/watch.py), the probes that decide
when the proxy routes to a new container, and the retries that cover the seconds the site is away."""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("deploy_watch", ROOT / "scripts/deploy/watch.py")
watch = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = watch  # dataclasses looks its module up there
_spec.loader.exec_module(watch)


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


# ── scripts/deploy/watch.py ──────────────────────────────────────────────────

def test_an_outage_runs_from_the_first_failed_answer_to_the_next_good_one():
    samples = [(0, "200"), (1, "200"), (2, "502"), (3, "503"), (4, "503"), (5, "timeout"), (6, "200"), (7, "200")]
    [outage] = watch.outages(samples)
    assert (outage.start, outage.end, outage.seconds) == (2, 6, 4)
    assert outage.answers == {"502": 1, "503": 2, "timeout": 1}
    assert watch.outages([(0, "200"), (1, "200")]) == []
    # two separate ones are not added up; one still open at the end stops at the last answer
    first, second = watch.outages([(0, "503"), (2, "200"), (5, "502"), (9, "502")])
    assert (first.seconds, second.seconds) == (2, 4)


class _Site:
    """A site whose /health gives the scripted answers, one per second of a clock that only sleep() moves."""

    def __init__(self, answers):
        self.answers, self.now, self.said = list(answers), 0.0, []

    def ask(self, url):
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return ("200", answer) if answer in ("old", "new") else (answer, "")

    def run(self, limit=600.0):
        def sleep(seconds):
            self.now += seconds
        return watch.watch("https://example.test/health", "new", limit, ask=self.ask, clock=lambda: self.now,
                           sleep=sleep, say=self.said.append)


def test_the_watch_ends_once_the_new_build_answers_three_times_in_a_row():
    site = _Site(["old", "old", "502", "503", "503", "new"])
    served, samples = site.run()
    assert served
    assert [answer for _, answer in samples] == ["200", "200", "502", "503", "503", "200", "200", "200"]
    assert [at for at, _ in samples] == [0, 1, 2, 3, 4, 5, 6, 7]  # one question a second
    [outage] = watch.outages(samples)
    assert outage.seconds == 3
    # each change of answer is said once, not each question
    assert len(site.said) == 4 and "build new" in site.said[-1]


def test_a_build_that_never_arrives_and_an_outage_too_long_both_fail_the_job():
    site = _Site(["old"])
    served, samples = site.run(limit=30)
    assert not served and len(samples) == 30
    passed, summary = watch.report(False, [], "new", 45)
    assert not passed and "never served" in summary

    long = [watch.Outage(100, 150, {"503": 50})]
    passed, summary = watch.report(True, long, "new", 45)
    assert not passed and "50 s is more than the 45 s" in summary
    passed, summary = watch.report(True, [watch.Outage(100, 112, {"502": 1, "503": 11})], "new", 45)
    assert passed and "12 s without an answer" in summary and "1 × 502, 11 × 503" in summary
    passed, summary = watch.report(True, [], "new", 45)
    assert passed and "No failed answer" in summary


def test_an_answer_that_is_not_the_apps_json_counts_as_unreachable(monkeypatch):
    class _Page:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *args):
            return b"<html>a proxy's page</html>"

    monkeypatch.setattr(watch.urllib.request, "urlopen", lambda *args, **kwargs: _Page())
    assert watch.ask("https://example.test/health") == ("unreachable", "")


# ── what shortens the outage, and what covers it ─────────────────────────────

def test_the_deploy_job_measures_the_outage_under_what_the_retries_cover():
    step = _read(".github/workflows/deploy.yml")
    limit = int(re.search(r"scripts/deploy/watch\.py .*--max-outage (\d+)", step)[1])
    worker = int(re.search(r"var BUDGET = (\d+)", _read("app/static/sw.js"))[1]) / 1000
    shell = int(re.search(r"RETRY_BUDGET_MS = (\d+)", _read("mobile/src/retry.ts"))[1]) / 1000
    refresh = int(re.search(r"var PATIENCE = (\d+)", _read("app/static/js/refresh.js"))[1]) / 1000
    assert limit < min(worker, shell, refresh), "an outage the job accepts must be one every retry outlasts"
    assert worker == shell == refresh == 60


def test_the_app_probe_waits_for_the_app_and_the_database_probes_are_frequent():
    services = yaml.safe_load(_read("docker-compose.prod.yml"))["services"]
    probe = services["app"]["healthcheck"]
    # it asks every second instead of failing once and coming back an interval later, on any Docker version
    # (start_interval, which would do it, needs Docker 25: an older engine refuses the whole file)
    assert "start_interval" not in probe
    command = probe["test"][1]
    assert probe["test"][0] == "CMD-SHELL" and "sleep 1" in command and "curl -fsS -m 1" in command
    assert command.endswith("exit 1") and "http://localhost:8000/health && exit 0" in command
    assert (probe["interval"], probe["start_period"]) == ("10s", "60s")
    # the image's own probe is the same one
    assert command in _read("Dockerfile").replace("\\\n", "")
    for name in ("db", "redis"):  # `app` starts when both are healthy
        assert services[name]["healthcheck"]["interval"] == "2s", name
        assert services[name]["healthcheck"]["retries"] == 15, name


def test_the_app_probe_succeeds_as_soon_as_health_answers(tmp_path):
    """The probe's shell, run for real against a `curl` that fails twice then succeeds."""
    command = yaml.safe_load(_read("docker-compose.prod.yml"))["services"]["app"]["healthcheck"]["test"][1]
    fake = tmp_path / "curl"
    fake.write_text('#!/bin/sh\nn=$(cat "$COUNT" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "$COUNT"\n'
                    '[ "$n" -ge "$READY_AT" ]\n')
    fake.chmod(0o755)
    sleeper = tmp_path / "sleep"
    sleeper.write_text("#!/bin/sh\nexit 0\n")
    sleeper.chmod(0o755)

    def run(ready_at):
        count = tmp_path / f"count-{ready_at}"
        env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "COUNT": str(count), "READY_AT": str(ready_at)}
        code = subprocess.run(["sh", "-c", command], env=env, check=False).returncode
        return code, int(count.read_text())

    assert run(1) == (0, 1)   # up: one question
    assert run(3) == (0, 3)   # starting: answered at the third, without waiting for the next probe
    assert run(99) == (1, 8)  # down: eight questions, then unhealthy


# ── the service worker ───────────────────────────────────────────────────────

async def test_the_service_worker_is_served_at_the_root_and_never_cached(client):
    response = await client.get("/sw.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["cache-control"] == "no-cache"
    assert response.text == _read("app/static/sw.js")


def test_the_service_worker_only_repeats_a_page_the_proxy_answered():
    worker = _read("app/static/sw.js")
    # a navigation, a GET: nothing is sent twice, and no other request is touched
    assert "event.request.mode === 'navigate' && event.request.method === 'GET'" in worker
    assert worker.count("respondWith") == 1 and "caches.open" not in worker and "cache.put" not in worker
    # the app's own 404 and 503 carry its Content-Security-Policy: they are final
    assert "[404, 502, 503, 504].indexOf(response.status) >= 0 && !response.headers.has('content-security-policy')" in worker
    base = _read("app/templates/base.html")
    assert "navigator.serviceWorker.register('/sw.js')" in base


async def test_every_answer_of_the_app_carries_the_header_the_worker_tells_it_by(client):
    for path in ("/zz-not-a-page", "/health", "/"):
        response = await client.get(path)
        assert "content-security-policy" in response.headers, path
