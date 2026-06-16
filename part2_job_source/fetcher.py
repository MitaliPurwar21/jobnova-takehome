"""Page fetching.

Defaults to a plain HTTP request (fast, no browser needed). If a page comes
back with almost no usable HTML — usually a JS-rendered site — and Playwright is
installed, it retries with a headless browser.
"""

import logging

import httpx

logger = logging.getLogger("job-source.fetcher")

# A normal-looking browser UA; some sites return stripped pages to obvious bots.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_html(url: str, render: bool = False, timeout: float = 20.0) -> tuple[str, str]:
    """Return (final_url, html) for a page.

    render=True forces the headless-browser path; otherwise it's only used as a
    fallback when the plain request looks empty.
    """
    if not render:
        try:
            resp = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=timeout)
            resp.raise_for_status()
            html = resp.text
            if len(html) > 2000 or "<a " in html:
                return str(resp.url), html
            logger.info("Page looked thin (%s chars) — trying a rendered fetch", len(html))
        except httpx.HTTPError as e:
            logger.info("Plain fetch failed (%s) — trying a rendered fetch", e)

    rendered = _fetch_rendered(url, timeout)
    if rendered is not None:
        return rendered
    # Last resort: return whatever the plain request gave us.
    resp = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=timeout)
    return str(resp.url), resp.text


def _fetch_rendered(url: str, timeout: float) -> tuple[str, str] | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.info("Playwright not installed — skipping rendered fetch")
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=HEADERS["User-Agent"])
            # domcontentloaded is reliable; then give scripts a moment to inject
            # links. networkidle alone times out on heavy enterprise pages.
            page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            html = page.content()
            final_url = page.url
            browser.close()
            return final_url, html
    except Exception as e:
        logger.warning("Rendered fetch failed: %s", e)
        return None
