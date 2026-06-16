"""Step 1b — turn a company name into its website URL.

Uses Clearbit's autocomplete endpoint (no key required) to map a name to a
domain, and falls back to a DuckDuckGo search if that misses.
"""

import logging
import re
from urllib.parse import unquote

import httpx

from fetcher import HEADERS

logger = logging.getLogger("job-source.company")

CLEARBIT = "https://autocomplete.clearbit.com/v1/companies/suggest"
DDG = "https://html.duckduckgo.com/html/"


def _clearbit(name: str) -> str | None:
    try:
        resp = httpx.get(CLEARBIT, params={"query": name}, timeout=15)
        resp.raise_for_status()
        results = resp.json()
    except Exception as e:
        logger.info("Clearbit lookup failed: %s", e)
        return None

    if not results:
        return None
    # Prefer an exact-ish name match, otherwise take the top suggestion.
    lowered = name.lower()
    for r in results:
        if r.get("name", "").lower() == lowered and r.get("domain"):
            return "https://" + r["domain"]
    domain = results[0].get("domain")
    return "https://" + domain if domain else None


def _duckduckgo(name: str) -> str | None:
    try:
        resp = httpx.post(
            DDG,
            data={"q": f"{name} official website"},
            headers=HEADERS,
            follow_redirects=True,
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.info("DuckDuckGo lookup failed: %s", e)
        return None

    # Result links are wrapped as /l/?uddg=<encoded real url>.
    m = re.search(r"uddg=([^&\"]+)", resp.text)
    if m:
        return unquote(m.group(1))
    return None


def resolve_website(company_name: str) -> str | None:
    """Best-effort company website URL for a name."""
    logger.info(">> Resolving website for %r", company_name)
    site = _clearbit(company_name) or _duckduckgo(company_name)
    if site:
        logger.info(">> Company website: %s", site)
    else:
        logger.warning("Could not resolve a website for %r", company_name)
    return site
