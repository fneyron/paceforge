"""services/safe_fetch: the one place a user-typed address is requested (Coaching › « Mon agenda »). Every refusal,
with the resolver and the network replaced: https only, a public host name, every resolved address public, the
connection pinned to the address that was checked, each redirect checked again (3 at most), 10 s, 2 MB read as a
stream, and a refusal that never carries the response's body."""
import asyncio

import httpx
import pytest

from app.services import safe_fetch as sf

CALENDAR = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"
PUBLIC = "93.184.216.34"


@pytest.fixture
def net(monkeypatch):
    """A fake resolver ({host: [addresses]}) and a fake network (a handler per test), with what was asked of both."""
    class Net:
        hosts: dict = {"cal.example.org": [PUBLIC]}
        resolved: list = []
        requests: list = []
        handler = staticmethod(lambda request: httpx.Response(200, content=CALENDAR))

    async def resolve(host):
        Net.resolved.append(host)
        if host not in Net.hosts:
            raise OSError("unknown host")
        return list(Net.hosts[host])

    def transport(request: httpx.Request) -> httpx.Response:
        Net.requests.append(request)
        return Net.handler(request)
    Net.hosts, Net.resolved, Net.requests = {"cal.example.org": [PUBLIC]}, [], []
    monkeypatch.setattr(sf, "_resolve", resolve)
    monkeypatch.setattr(sf, "_transport", httpx.MockTransport(transport))
    return Net


async def _refused(url: str, **kw) -> str:
    with pytest.raises(sf.Refused) as e:
        await sf.fetch(url, **kw)
    return e.value.code


# ── the address as typed ────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://cal.example.org/a.ics", "ftp://cal.example.org/a.ics", "file:///etc/passwd", "gopher://cal.example.org/",
    "javascript:alert(1)", "//cal.example.org/a.ics", "cal.example.org/a.ics", "", "   ", "https://",
    "https://user:secret@cal.example.org/a.ics", "https://cal.example.org:8443/a.ics", "https://cal.example.org:22/",
    "https://cal.example.org/a b.ics", "https://cal.example.org/a\n.ics", "https://" + "a" * 1100 + ".example.org/",
    "https://cal.example.org:notaport/a.ics",
])
async def test_only_a_plain_https_address_is_accepted(url, net):
    assert await _refused(url) == "url"
    assert net.resolved == [] and net.requests == [], "refused before any lookup or request"


@pytest.mark.parametrize("url", [
    "https://127.0.0.1/a.ics", "https://10.0.0.5/a.ics", "https://[::1]/a.ics", "https://169.254.169.254/latest/",
    "https://93.184.216.34/a.ics", "https://localhost/a.ics", "https://intranet/a.ics", "https://db.internal/a.ics",
    "https://printer.local/a.ics", "https://router.lan/", "https://paceforge.example/coaching/cal/x.ics",
    "https://PaceForge.Example./coaching/cal/x.ics",
])
async def test_an_ip_literal_an_internal_name_or_this_site_is_refused(url, net):
    assert await _refused(url, own_host="paceforge.example") == "host"
    assert net.resolved == [] and net.requests == []


def test_webcal_is_read_as_https_and_the_address_is_normalised():
    assert sf.clean_url("webcal://Cal.Example.org/x/basic.ics?key=1#frag") == "https://cal.example.org/x/basic.ics?key=1"
    assert sf.clean_url(" https://cal.example.org:443 ") == "https://cal.example.org/"
    assert sf.clean_url("WEBCAL://p12-caldav.icloud.com/published/2/abc") == "https://p12-caldav.icloud.com/published/2/abc"


# ── what the host resolves to ───────────────────────────────────────────────

@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0", "224.0.0.1",
    "240.0.0.1", "255.255.255.255", "192.0.2.10", "198.18.0.1", "::1", "::", "fe80::1", "fc00::1", "fd12:3456::1",
    "ff02::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "64:ff9b::a00:1", "2002:a00:1::1", "2001:db8::1",
    "2001:0:4136:e378:8000:63bf:3fff:fdd2", "fe80::1%eth0", "not-an-address",
])
async def test_a_host_that_resolves_to_a_non_public_address_is_refused(address, net):
    assert not sf.is_public(address)
    net.hosts["cal.example.org"] = [address]
    assert await _refused("https://cal.example.org/a.ics") == "host"
    assert net.requests == [], "never connected to"


@pytest.mark.parametrize("address", ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_a_public_address_is_accepted(address):
    assert sf.is_public(address)


async def test_every_address_of_the_host_must_be_public(net):
    """One private answer among public ones (a rebinding name) refuses the host."""
    net.hosts["cal.example.org"] = [PUBLIC, "10.0.0.7"]
    assert await _refused("https://cal.example.org/a.ics") == "host"
    net.hosts["cal.example.org"] = []
    assert await _refused("https://cal.example.org/a.ics") == "dns"
    assert await _refused("https://unknown.example.org/a.ics") == "dns"
    assert net.requests == []


async def test_the_connection_goes_to_the_address_that_was_checked(net):
    """The request's URL carries the checked IP; the Host header and the TLS name carry the host: a second DNS
    answer cannot move the connection."""
    net.hosts["cal.example.org"] = [PUBLIC, "8.8.4.4"]
    assert await sf.fetch("webcal://cal.example.org/x/basic.ics?token=abc") == CALENDAR
    (request,) = net.requests
    assert str(request.url) == f"https://{PUBLIC}/x/basic.ics?token=abc"
    assert request.headers["host"] == "cal.example.org" and request.extensions["sni_hostname"] == "cal.example.org"
    assert net.resolved == ["cal.example.org"], "resolved once, here"
    net.hosts["v6.example.org"] = ["2606:4700:4700::1111"]
    await sf.fetch("https://v6.example.org/a.ics")
    assert str(net.requests[-1].url) == "https://[2606:4700:4700::1111]/a.ics"


def test_the_client_takes_no_proxy_from_the_environment_and_follows_nothing_by_itself():
    import inspect

    source = inspect.getsource(sf.fetch)
    assert "trust_env=False" in source and "follow_redirects=False" in source


# ── redirects ───────────────────────────────────────────────────────────────

async def test_a_redirect_is_followed_by_hand_and_checked_again(net):
    net.hosts["next.example.org"] = ["8.8.8.8"]

    def handler(request):
        if request.headers["host"] == "cal.example.org":
            return httpx.Response(302, headers={"location": "https://next.example.org/real.ics"})
        return httpx.Response(200, content=CALENDAR)
    net.handler = handler
    assert await sf.fetch("https://cal.example.org/a.ics") == CALENDAR
    assert [str(r.url) for r in net.requests] == [f"https://{PUBLIC}/a.ics", "https://8.8.8.8/real.ics"]
    assert net.resolved == ["cal.example.org", "next.example.org"]


@pytest.mark.parametrize("location,code", [
    ("http://cal.example.org/a.ics", "url"),  # a downgrade
    ("https://127.0.0.1/a.ics", "host"),
    ("https://169.254.169.254/latest/meta-data/", "host"),
    ("https://inside.example.org/a.ics", "host"),  # resolves to a private address
    ("https://gone.example.org/a.ics", "dns"),
    ("file:///etc/passwd", "url"),
    ("//localhost/a.ics", "host"),
    ("", "status"),
])
async def test_a_redirect_to_a_refused_place_is_refused(location, code, net):
    net.hosts["inside.example.org"] = ["192.168.0.10"]
    net.handler = lambda request: httpx.Response(301, headers={"location": location} if location else {})
    assert await _refused("https://cal.example.org/a.ics") == code
    assert len(net.requests) == 1, "the redirect's target was never requested"


async def test_three_redirects_at_most(net):
    hops = {"n": 0}

    def handler(request):
        hops["n"] += 1
        return httpx.Response(307, headers={"location": f"/hop{hops['n']}.ics"})
    net.handler = handler
    assert await _refused("https://cal.example.org/a.ics") == "redirect"
    assert len(net.requests) == sf.MAX_REDIRECTS + 1 == 4
    hops["n"] = 0
    net.requests.clear()
    net.handler = lambda request: (httpx.Response(200, content=CALENDAR) if request.url.path == "/hop3.ics"
                                   else handler(request))
    assert await sf.fetch("https://cal.example.org/a.ics") == CALENDAR, "exactly three are followed"


# ── status, size, time ──────────────────────────────────────────────────────

@pytest.mark.parametrize("status", [204, 304, 401, 403, 404, 410, 429, 500, 503])
async def test_anything_but_a_200_is_refused_without_its_body(status, net):
    net.handler = lambda request: httpx.Response(status, content=b"SECRET-INTERNAL-PAGE <html>admin</html>")
    with pytest.raises(sf.Refused) as e:
        await sf.fetch("https://cal.example.org/a.ics")
    assert e.value.code == "status"
    assert "SECRET" not in str(e.value) and "SECRET" not in repr(e.value) and e.value.args == ("status",)


async def test_two_megabytes_at_most_read_as_a_stream(net):
    class Endless(httpx.AsyncByteStream):
        sent = 0

        async def __aiter__(self):
            while True:  # a body that never ends: it must be cut, not awaited
                Endless.sent += 64 * 1024
                yield b"x" * 64 * 1024

    net.handler = lambda request: httpx.Response(200, stream=Endless())
    assert await _refused("https://cal.example.org/a.ics") == "too_big"
    assert sf.MAX_BYTES == 2 * 1024 * 1024 and Endless.sent <= sf.MAX_BYTES + 64 * 1024, "cut at the limit"
    net.handler = lambda request: httpx.Response(200, headers={"content-length": str(sf.MAX_BYTES + 1)}, content=b"")
    assert await _refused("https://cal.example.org/a.ics") == "too_big", "refused on its declared size alone"
    exact = b"y" * sf.MAX_BYTES
    net.handler = lambda request: httpx.Response(200, content=exact)
    assert await sf.fetch("https://cal.example.org/a.ics") == exact


async def test_ten_seconds_for_the_whole_exchange(net, monkeypatch):
    assert sf.TIMEOUT_S == 10
    monkeypatch.setattr(sf, "TIMEOUT_S", 0.05)

    async def slow(host):
        await asyncio.sleep(5)
        return [PUBLIC]
    monkeypatch.setattr(sf, "_resolve", slow)
    assert await _refused("https://cal.example.org/a.ics") == "timeout"

    async def resolve(host):
        return [PUBLIC]
    monkeypatch.setattr(sf, "_resolve", resolve)

    def stall(request):
        raise httpx.ReadTimeout("stalled")
    net.handler = stall
    assert await _refused("https://cal.example.org/a.ics") == "timeout"

    def broken(request):
        raise httpx.ConnectError("refused: 10.0.0.1 internal detail")
    net.handler = broken
    with pytest.raises(sf.Refused) as e:
        await sf.fetch("https://cal.example.org/a.ics")
    assert e.value.code == "network" and "10.0.0.1" not in str(e.value)


async def test_an_address_that_refuses_the_connection_hands_over_to_the_next_checked_one(net):
    """A host with an IPv6 and an IPv4 address, read from a machine without IPv6: the second address answers. Both
    were checked public first, the name stays in Host, and a private address in the list still refuses it all."""
    v6 = "2606:2800:220:1:248:1893:25c8:1946"
    net.hosts["cal.example.org"] = [v6, PUBLIC]

    def handler(request):
        if request.url.host != PUBLIC:
            raise httpx.ConnectError("no route")
        return httpx.Response(200, content=CALENDAR)
    net.handler = handler
    assert await sf.fetch("https://cal.example.org/a.ics") == CALENDAR
    assert [r.url.host for r in net.requests] == [v6, PUBLIC]
    assert all(r.headers["host"] == "cal.example.org" for r in net.requests)

    net.hosts["cal.example.org"] = [v6, "10.0.0.5"]
    net.requests.clear()
    assert await _refused("https://cal.example.org/a.ics") == "host" and net.requests == []
