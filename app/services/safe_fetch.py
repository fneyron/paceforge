"""Fetching an address a user typed (Coaching › « Mon agenda », docs/coaching.md): a server-side request forgery
surface, held tight. No other code fetches a user-supplied URL.

- https only (`webcal://` is rewritten to it), port 443, no credentials in the address, a host name with a dot (never
  an IP literal, never this site's own host), 1 024 characters at most;
- the host is resolved here and EVERY address it resolves to must be public: private, loopback, link-local, shared
  (100.64/10), multicast, reserved and unspecified ranges are refused, IPv4-mapped IPv6 read as its IPv4, 6to4, Teredo
  and NAT64 refused; the connection then goes to that very address (the URL carries the IP, the Host header and the
  TLS SNI carry the name: the certificate is still checked against the name), so a second DNS answer cannot move it;
- redirects are followed by hand, 3 at most, each one checked again from the start (scheme, host, resolution);
- no proxy from the environment, 10 s for the whole exchange, 2 MB at most, read as a stream and cut at the limit
  (after decompression);
- a refusal is a code (Refused.code), never the response's body, status line or headers.
"""
import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

TIMEOUT_S = 10
MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
MAX_URL = 1024
REDIRECTS = (301, 302, 303, 307, 308)
NAT64 = ipaddress.ip_network("64:ff9b::/96")
AGENT = "PaceForge (lecture de calendrier)"
_transport: httpx.AsyncBaseTransport | None = None  # the tests' seam (httpx.MockTransport)


class Refused(Exception):
    """Why an address was not read: url | host | dns | redirect | status | too_big | timeout | network."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def clean_url(raw: str | None, own_host: str | None = None) -> str:
    """The address as it will be fetched, or Refused("url" | "host"): https (webcal:// rewritten), port 443, no
    credentials, a dotted host name that is neither an IP literal nor `own_host` (this site's)."""
    text = (raw or "").strip()
    if not text or len(text) > MAX_URL or any(ch.isspace() or ord(ch) < 32 for ch in text):
        raise Refused("url")
    if text.lower().startswith("webcal://"):
        text = "https://" + text[len("webcal://"):]
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as e:
        raise Refused("url") from e
    host = (parts.hostname or "").rstrip(".").lower()
    if parts.scheme.lower() != "https" or not host or parts.username is not None or parts.password is not None:
        raise Refused("url")
    if port not in (None, 443):
        raise Refused("url")
    try:
        ipaddress.ip_address(host.strip("[]"))
        raise Refused("host")  # an IP literal: a calendar has a name
    except ValueError:
        pass
    if "." not in host or host.endswith((".local", ".localhost", ".internal", ".lan", ".home", ".arpa")):
        raise Refused("host")
    if own_host and host == own_host.lower():
        raise Refused("host")
    try:
        host.encode("idna")
    except UnicodeError as e:
        raise Refused("url") from e
    path = parts.path or "/"
    return f"https://{host}{path}" + (f"?{parts.query}" if parts.query else "")


def is_public(value: str) -> bool:
    """Whether an address a host resolved to may be connected to: a public unicast address only."""
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return is_public(str(ip.ipv4_mapped))
        if ip.sixtofour is not None or ip.teredo is not None or ip in NAT64:
            return False
    return ip.is_global and not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                                 or ip.is_reserved or ip.is_unspecified)


async def _resolve(host: str) -> list[str]:
    """The addresses `host` resolves to (the system resolver); a seam the tests replace."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


async def pinned_address(host: str) -> str:
    """The address the connection will go to: the host's first one, once ALL of them are public."""
    try:
        addresses = await _resolve(host)
    except (OSError, UnicodeError) as e:
        raise Refused("dns") from e
    if not addresses:
        raise Refused("dns")
    if not all(is_public(a) for a in addresses):
        raise Refused("host")
    return addresses[0]


async def _read(client: httpx.AsyncClient, url: str) -> tuple[int, str | None, bytes]:
    """One request to `url`'s host at its pinned address: (status, Location, body); the body only for a 200, read
    as a stream and refused past MAX_BYTES."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    address = await pinned_address(host)
    literal = f"[{address}]" if ":" in address else address
    target = f"https://{literal}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")
    request = client.build_request("GET", target, headers={
        "Host": host, "Accept": "text/calendar, text/plain;q=0.5", "User-Agent": AGENT},
        extensions={"sni_hostname": host})
    response = await client.send(request, stream=True)
    try:
        if response.status_code in REDIRECTS:
            return response.status_code, response.headers.get("location"), b""
        if response.status_code != 200:
            raise Refused("status")
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BYTES:
            raise Refused("too_big")
        size, chunks = 0, []
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_BYTES:
                raise Refused("too_big")
            chunks.append(chunk)
        return 200, None, b"".join(chunks)
    finally:
        await response.aclose()


async def fetch(raw_url: str, own_host: str | None = None) -> bytes:
    """The body at a user-typed https address, under every rule of this module; Refused otherwise."""
    url = clean_url(raw_url, own_host)
    try:
        async with asyncio.timeout(TIMEOUT_S):
            async with httpx.AsyncClient(transport=_transport, timeout=TIMEOUT_S, follow_redirects=False,
                                         trust_env=False) as client:
                for _hop in range(MAX_REDIRECTS + 1):
                    status, location, body = await _read(client, url)
                    if status == 200:
                        return body
                    if not location:
                        raise Refused("status")
                    url = clean_url(urljoin(url, location), own_host)  # checked again from the start
                raise Refused("redirect")
    except TimeoutError as e:
        raise Refused("timeout") from e
    except httpx.TimeoutException as e:
        raise Refused("timeout") from e
    except httpx.HTTPError as e:
        raise Refused("network") from e
