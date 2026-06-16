"""Steps 2 & 3 — the web-navigation agent.

Given a company website, it finds the careers page, then finds one open
position on it. At each hop it collects the candidate links and decides which
to follow. The decision uses a Groq LLM when GROQ_API_KEY is set; otherwise it
falls back to keyword heuristics so the agent still runs with no key at all.
"""

import json
import logging
import os
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from fetcher import fetch_html
from verify import reachable_final, verify_urls

logger = logging.getLogger("job-source.web_agent")

# Common careers paths to probe when the homepage doesn't link one directly
# (big-company homepages often render their menus in JavaScript).
CAREERS_PATHS = (
    "/careers",
    "/careers/search",
    "/jobs",
    "/join-us",
    "/about/careers",
    "/en/careers",
)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

# Hints for the heuristic fallback / link filtering.
CAREER_HINTS = (
    "career",
    "careers",
    "jobs",
    "join",
    "work-with-us",
    "we're-hiring",
    "were-hiring",
    "hiring",
    "recruit",
    "vacancies",
    "openings",
    "opportunities",
    "open-positions",
    "life-at",
)
# Hosts/paths that usually mean an actual job posting (not a careers index).
POSTING_HINTS = (
    "lever.co",
    "greenhouse.io",
    "boards.greenhouse",
    "myworkdayjobs",
    "ashbyhq",
    "smartrecruiters",
    "bamboohr",
    "workable.com",
    "hireclick",
    "jobvite",
    "icims",
    "recruitee",
    "breezy",
    "applytojob",
    "teamtailor",
    "/job/",
    "/jobs/",
    "/jb/",
    "/position",
    "/opening",
    "/vacancy",
    "gh_jid=",
)

# Static assets to ignore when harvesting URLs out of page text.
_ASSET_RE = re.compile(
    r"\.(?:js|css|png|jpe?g|gif|svg|webp|ico|pdf|woff2?|eot|otf|ttf|mp4)(?:$|\?)", re.I
)

# Link text that labels a button rather than a role, so it makes a poor title.
_GENERIC_TEXT = {"apply", "apply now", "view", "view info", "view job", "view details",
                 "learn more", "details", "read more", "see more", "more", ""}


def extract_links(html: str, base_url: str) -> list[dict]:
    """Return de-duplicated, absolute links as {text, url} dicts."""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    links: list[dict] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        text = " ".join(a.get_text(" ", strip=True).split())[:80]
        links.append({"text": text, "url": url})
    return links


def _score(link: dict, hints: tuple[str, ...]) -> int:
    blob = (link["text"] + " " + link["url"]).lower()
    return sum(1 for h in hints if h in blob)


class LLM:
    """Thin wrapper around Groq's OpenAI-compatible chat endpoint."""

    def __init__(self) -> None:
        self.api_key = os.getenv("GROQ_API_KEY")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def pick_link(self, goal: str, links: list[dict]) -> str | None:
        """Ask the model which link best matches the goal. Returns a URL or None."""
        if not self.available or not links:
            return None
        numbered = "\n".join(f"{i}. {l['text']} -> {l['url']}" for i, l in enumerate(links))
        prompt = (
            f"You are a web-navigation agent. Goal: {goal}\n\n"
            "Here are the links on the current page:\n"
            f"{numbered}\n\n"
            'Reply with ONLY a JSON object like {"index": <number>} for the single '
            'best link, or {"index": -1} if none fit.'
        )
        try:
            resp = httpx.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": GROQ_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
                timeout=30,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            idx = int(json.loads(content).get("index", -1))
        except Exception as e:
            logger.info("LLM pick failed (%s) — using heuristics", e)
            return None
        if 0 <= idx < len(links):
            return links[idx]["url"]
        return None


def _best_by_heuristic(links: list[dict], hints: tuple[str, ...]) -> str | None:
    ranked = sorted(links, key=lambda l: _score(l, hints), reverse=True)
    if ranked and _score(ranked[0], hints) > 0:
        return ranked[0]["url"]
    return None


def _looks_specific(url: str) -> bool:
    """True if a URL looks like a single posting rather than a board/index."""
    p = urlparse(url)
    host = p.netloc
    segs = [s for s in p.path.split("/") if s]
    if "lever.co" in host or "ashbyhq" in host or "smartrecruiters" in host:
        return len(segs) >= 2
    if "greenhouse" in host:
        return "jobs" in segs
    if "myworkdayjobs" in host:
        return "/job/" in p.path
    if "gh_jid=" in url:
        return True
    # Generic: a real posting usually carries an id-like segment.
    return any(re.search(r"\d{4,}|[0-9a-f]{8,}", s) for s in segs)


def _guess_career_page(root_url: str) -> str | None:
    """Probe common careers paths / subdomains and return the first that resolves."""
    p = urlparse(root_url)
    base = f"{p.scheme}://{p.netloc}"
    domain = p.netloc.replace("www.", "")
    candidates = [base + path for path in CAREERS_PATHS]
    candidates += [f"https://careers.{domain}", f"https://jobs.{domain}"]
    for url in candidates:
        final = reachable_final(url)
        if final:
            return final
    return None


def find_career_page(website: str, llm: LLM, render: bool = False) -> str | None:
    """Step 2 — locate the careers page starting from the company homepage."""
    logger.info(">> STEP 2: Finding career page from %s", website)
    final_url, html = fetch_html(website, render=render)
    links = extract_links(html, final_url)

    # Same-domain career-ish links are the strongest signal; offer those first.
    host = urlparse(final_url).netloc
    candidates = [l for l in links if _score(l, CAREER_HINTS) > 0] or links
    candidates = sorted(candidates, key=lambda l: (urlparse(l["url"]).netloc != host))

    choice = llm.pick_link(
        "Find the company's careers / jobs page.", candidates[:40]
    ) or _best_by_heuristic(candidates, CAREER_HINTS)

    # Homepage didn't surface one (common for JS-rendered sites) — probe known paths.
    if not choice:
        logger.info(">> No careers link in homepage HTML — probing common paths")
        choice = _guess_career_page(final_url)

    if choice:
        logger.info(">> Career page: %s", choice)
    else:
        logger.warning("No career page found for %s", website)
    return choice


def _harvest_posting_urls(html: str) -> list[dict]:
    """Pull posting-looking URLs out of the raw HTML, not just <a> tags.

    Some careers widgets (e.g. hireclick) render job links via JavaScript, so
    the URLs sit in scripts/data attributes rather than anchors.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for raw in re.findall(r"https?://[^\s\"'<>()\\]+", html):
        url = raw.split("#")[0].rstrip(".,);'\"")
        if not url or url in seen or _ASSET_RE.search(url):
            continue
        if _score({"text": "", "url": url}, POSTING_HINTS) > 0:
            seen.add(url)
            out.append({"text": "", "url": url})
    return out


def _posting_candidates(html: str, base_url: str) -> list[dict]:
    """Posting-hint links from <a> tags plus any harvested from page text."""
    candidates = [l for l in extract_links(html, base_url) if _score(l, POSTING_HINTS) > 0]
    seen = {c["url"] for c in candidates}
    for h in _harvest_posting_urls(html):
        if h["url"] not in seen:
            seen.add(h["url"])
            candidates.append(h)
    return candidates


# ATS boards that put the company slug as the first path segment (not a role).
_ORG_IN_PATH_HOSTS = ("lever.co", "greenhouse.io", "ashbyhq", "smartrecruiters",
                      "workable", "recruitee", "breezy")


def _slug_title(url: str) -> str | None:
    """Derive a readable title from a posting URL's slug, if it has real words."""
    parsed = urlparse(url)
    segs = [s for s in parsed.path.split("/") if s]
    if any(h in parsed.netloc for h in _ORG_IN_PATH_HOSTS) and segs:
        segs = segs[1:]  # first segment is the company slug, not a role
    skip = {"jb", "view", "jobs", "job", "results", "careers", "position",
            "positions", "opening", "p", "apply"}
    # Keep segments with letters that aren't just hex ids / UUIDs.
    words = [
        s for s in segs
        if s.lower() not in skip
        and re.search(r"[a-z]", s, re.I)
        and not re.fullmatch(r"[0-9a-f]{6,}(?:-[0-9a-f]{3,})*", s, re.I)
    ]
    if words:
        return re.sub(r"[-_]+", " ", max(words, key=len)).strip().title() or None
    return None


def _canonical_posting(url: str) -> str:
    """Strip query/fragment and a trailing /apply so a posting and its apply
    button collapse to the same entry."""
    u = url.split("#")[0].split("?")[0]
    u = re.sub(r"/apply/?$", "", u)
    return u.rstrip("/")


def _dedupe_positions(candidates: list[dict]) -> list[dict]:
    """Collapse duplicate links to one {title, url} per posting."""
    by_url: dict[str, dict] = {}
    for c in candidates:
        url = _canonical_posting(c["url"])
        text = c.get("text", "").strip()
        entry = by_url.setdefault(url, {"title": None, "url": url})
        if text and text.lower() not in _GENERIC_TEXT and not entry["title"]:
            entry["title"] = text[:80]
    for url, entry in by_url.items():
        entry["title"] = entry["title"] or _slug_title(url)
    return list(by_url.values())


def find_open_positions(
    career_url: str, llm: LLM, render: bool = False, max_hops: int = 3
) -> list[dict]:
    """Step 3 — find the open positions, starting from the careers page.

    Careers pages often link out to an applicant-tracking board (Lever,
    Greenhouse, ...) that is itself just a list, so this follows up to a few
    hops until it reaches actual postings. If the static page has no postings at
    all, it retries with a headless browser, since some boards inject their
    listings with JavaScript. Returns every verified posting as {title, url}.
    """
    logger.info(">> STEP 3: Finding open positions from %s", career_url)
    url = career_url
    for _ in range(max_hops):
        final_url, html = fetch_html(url, render=render)
        candidates = _posting_candidates(html, final_url)

        if not candidates and not render:
            logger.info(">> No postings in static HTML — rendering %s", url)
            final_url, html = fetch_html(url, render=True)
            candidates = _posting_candidates(html, final_url)

        candidates = [c for c in candidates if c["url"].rstrip("/") != final_url.rstrip("/")]

        specific = [c for c in candidates if _looks_specific(c["url"])]
        if specific:
            positions = _dedupe_positions(specific)
            # Only keep postings that actually resolve, so a malformed or stale
            # link (common on JS job-search portals) is never returned.
            reachable = verify_urls([p["url"] for p in positions[:25]])
            positions = [p for p in positions if reachable.get(p["url"])]
            if positions:
                logger.info(">> Found %d open position(s)", len(positions))
                return positions

        # No single posting yet — follow a board/index link one level deeper.
        nxt = llm.pick_link(
            "Pick the link most likely to lead to individual job postings.",
            candidates[:40],
        ) or _best_by_heuristic(candidates, POSTING_HINTS)
        if not nxt or nxt.rstrip("/") == url.rstrip("/"):
            break
        logger.info(">> Following %s to look for postings", nxt)
        url = nxt

    logger.warning("No open positions found from %s", career_url)
    return []


def find_open_position(career_url: str, llm: LLM, render: bool = False) -> str | None:
    """Convenience wrapper: the single open position URL the spec asks for."""
    positions = find_open_positions(career_url, llm, render=render)
    return positions[0]["url"] if positions else None
