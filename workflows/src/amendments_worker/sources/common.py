"""Shared constants and helpers for the Camera sources: HTTP politeness, hashes, text normalization."""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
import time
import unicodedata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

# NOTE: httpx is imported lazily inside functions on purpose: httpx subclasses
# urllib.request.Request at import time, and urllib.request is restricted inside
# the Temporal workflow sandbox, so a module-level `import httpx` in any module
# reachable from a workflow module makes the worker fail validation at startup.

logger = logging.getLogger(__name__)

CAMERA_USER_AGENT = "emendamentopoli-research/0.1"
CAMERA_MIN_INTERVAL_S = 1.0          # minimum gap between two request starts to the same host
CAMERA_CONCURRENCY_PER_HOST = 2
CAMERA_RETRY_STATUSES = frozenset({403, 429, 500, 502, 503, 504})
CAMERA_BACKOFF_S = (1.0, 2.0, 4.0, 8.0)   # waits before attempts 2..5
CAMERA_MAX_ATTEMPTS = 5
CAMERA_HTML_TIMEOUT_S = (10.0, 60.0, 10.0, 10.0)    # connect, read, write, pool
CAMERA_SPARQL_TIMEOUT_S = (10.0, 120.0, 10.0, 10.0)
SPARQL_CAMERA_ENDPOINT = "https://dati.camera.it/sparql"


def _timeout(spec: tuple[float, float, float, float]) -> httpx.Timeout:
    import httpx

    return httpx.Timeout(connect=spec[0], read=spec[1], write=spec[2], pool=spec[3])


def camera_html_timeout() -> httpx.Timeout:
    """Default timeout for HTML pages (read 60s)."""
    return _timeout(CAMERA_HTML_TIMEOUT_S)


def camera_sparql_timeout() -> httpx.Timeout:
    """Timeout for SPARQL queries (read 120s)."""
    return _timeout(CAMERA_SPARQL_TIMEOUT_S)
SUPABASE_PAGE = 1000                 # PostgREST max-rows on this project (verified)

_RE_TAG = re.compile(r"<[^>]+>")


class HostLimiter:
    """Per-host politeness: one asyncio.Semaphore(CAMERA_CONCURRENCY_PER_HOST) and one asyncio.Lock + last-start
    monotonic timestamp per host. acquire(host) waits so that two request starts to the same host are
    >= CAMERA_MIN_INTERVAL_S apart. Module-level singleton LIMITER = HostLimiter()."""

    def __init__(self) -> None:
        self._sems: dict[str, asyncio.Semaphore] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_start: dict[str, float] = {}

    async def acquire(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = CAMERA_MIN_INTERVAL_S - (time.monotonic() - self._last_start.get(host, 0.0))
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_start[host] = time.monotonic()

    @asynccontextmanager
    async def slot(self, host: str) -> AsyncIterator[None]:
        sem = self._sems.setdefault(host, asyncio.Semaphore(CAMERA_CONCURRENCY_PER_HOST))
        async with sem:
            await self.acquire(host)
            yield


LIMITER = HostLimiter()


async def polite_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
) -> httpx.Response:
    """GET with LIMITER, UA CAMERA_USER_AGENT (always set, overriding any caller value), retries on
    CAMERA_RETRY_STATUSES / httpx.TransportError with backoff CAMERA_BACKOFF_S (plus Retry-After when integer).
    304 is a success; any other non-2xx raises immediately; after CAMERA_MAX_ATTEMPTS raises RuntimeError.
    timeout=None means camera_html_timeout()."""
    import httpx

    if timeout is None:
        timeout = camera_html_timeout()
    host = urlsplit(url).netloc
    hdrs = {**(headers or {}), "User-Agent": CAMERA_USER_AGENT}
    last_error = "no attempt"
    for attempt in range(1, CAMERA_MAX_ATTEMPTS + 1):
        resp: httpx.Response | None = None
        try:
            async with LIMITER.slot(host):
                resp = await client.get(url, params=params, headers=hdrs, timeout=timeout)
        except httpx.TransportError as e:
            last_error = f"{type(e).__name__}: {e}"
        else:
            status = resp.status_code
            if 200 <= status < 300 or status == 304:
                logger.info("GET %s -> %s (%d bytes, attempt %d)", url, status, len(resp.content), attempt)
                return resp
            if status not in CAMERA_RETRY_STATUSES:
                raise RuntimeError(f"GET {url} -> {status}")
            last_error = str(status)
        if attempt < CAMERA_MAX_ATTEMPTS:
            wait = CAMERA_BACKOFF_S[attempt - 1]
            retry_after = resp.headers.get("Retry-After") if resp is not None else None
            if retry_after is not None and retry_after.strip().isdigit():
                wait = max(wait, float(retry_after))
            await asyncio.sleep(wait)
    raise RuntimeError(f"GET {url} failed after {CAMERA_MAX_ATTEMPTS} attempts: {last_error}")


def testo_hash(text: str | None) -> str | None:
    """sha256 hex of the normalized text, None for None/empty.

    Steps, in order: (1) unicodedata.normalize("NFC", text); (2) replace "’" and "‘" with "'";
    (3) str.lower(); (4) re.sub(r"\\s+", " ", s).strip(); (5) hashlib.sha256(s.encode("utf-8")).hexdigest().
    """
    if not text:
        return None
    s = unicodedata.normalize("NFC", text)
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def fold(text: str) -> str:
    """NFKD, drop combining marks, ’‘ -> ', nbsp -> space, lower, collapse whitespace, strip."""
    s = unicodedata.normalize("NFKD", text)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    s = s.replace("\xa0", " ")
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def yyyymmdd_to_iso(value: str) -> str | None:
    """'20261021' -> '2026-10-21'; '20230222-20230223' -> '2023-02-22'; anything without 8 leading digits -> None."""
    m = re.match(r"^(\d{4})(\d{2})(\d{2})", value)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def html_to_text(fragment: str) -> str:
    """<br> and </p> become newlines, tags are dropped, entities decoded, em/en/nbsp spaces become spaces,
    each line is whitespace-collapsed and stripped, empty lines dropped."""
    s = re.sub(r"<br\s*/?>", "\n", fragment)
    s = re.sub(r"</p>", "\n", s)
    s = _RE_TAG.sub("", s)
    s = html.unescape(s)
    s = s.replace("\u2003", " ").replace("\u2002", " ").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.split("\n")]
    return "\n".join(ln for ln in lines if ln)
