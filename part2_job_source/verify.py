"""Confirm that a URL is actually reachable.

LLMs (and loose heuristics) can produce plausible-but-dead links, so the
pipeline checks the URLs it returns instead of trusting them.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

import httpx

from fetcher import HEADERS

logger = logging.getLogger("job-source.verify")


def verify_url(url: str | None, timeout: float = 8.0) -> bool:
    """True if the URL responds with a non-error status.

    Tries HEAD first; some servers reject it (405/501), so fall back to GET.
    """
    if not url or not url.startswith("http"):
        return False
    try:
        resp = httpx.head(url, headers=HEADERS, follow_redirects=True, timeout=timeout)
        if resp.status_code in (405, 501) or resp.status_code >= 400:
            resp = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=timeout)
        return resp.status_code < 400
    except httpx.HTTPError:
        return False


def reachable_final(url: str | None, timeout: float = 8.0) -> str | None:
    """Return the final URL (after redirects) if it resolves, else None."""
    if not url or not url.startswith("http"):
        return None
    try:
        resp = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=timeout)
        return str(resp.url) if resp.status_code < 400 else None
    except httpx.HTTPError:
        return None


def verify_urls(urls: list[str]) -> dict[str, bool]:
    """Verify several URLs in parallel."""
    urls = [u for u in urls if u]
    if not urls:
        return {}
    with ThreadPoolExecutor(max_workers=min(8, len(urls))) as pool:
        return dict(zip(urls, pool.map(verify_url, urls)))
