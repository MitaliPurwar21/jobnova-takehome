"""Step 1 — pull the company name (and its LinkedIn page) from a LinkedIn job URL.

LinkedIn blocks logged-out scraping of the normal job page, but the public
"jobs-guest" posting endpoint returns the same job card without any login, which
is enough to read the company name. No API key needed.

A third-party crawler API can be plugged in via LINKEDIN_API_URL if you have one,
but it's optional — the guest endpoint is the default.
"""

import logging
import os
import re

from bs4 import BeautifulSoup

from fetcher import HEADERS

import httpx

logger = logging.getLogger("job-source.linkedin")

GUEST_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"


def extract_job_id(job_url: str) -> str | None:
    """Pull the numeric job ID out of a LinkedIn job URL."""
    # .../jobs/view/<id>/ or ...?currentJobId=<id> or .../view/title-<id>
    for pattern in (r"/jobs/view/(?:[^/]*-)?(\d+)", r"currentJobId=(\d+)", r"/(\d{6,})"):
        m = re.search(pattern, job_url)
        if m:
            return m.group(1)
    return None


def parse_company(html: str) -> dict:
    """Read the company name and LinkedIn company URL out of a job card."""
    soup = BeautifulSoup(html, "html.parser")

    org = soup.select_one("a.topcard__org-name-link") or soup.select_one(
        ".topcard__flavor a"
    )
    company_name = org.get_text(strip=True) if org else None
    company_linkedin_url = org["href"].split("?")[0] if org and org.has_attr("href") else None

    if not company_name:
        # Fall back to the page title, usually "Title hiring Company in City".
        title = soup.title.get_text(strip=True) if soup.title else ""
        m = re.search(r"hiring .*? (?:at|-) (.+?) (?:in|\|)", title)
        if m:
            company_name = m.group(1).strip()

    job_title_el = soup.select_one(".topcard__title, h1")
    job_title = job_title_el.get_text(strip=True) if job_title_el else None

    return {
        "company_name": company_name,
        "company_linkedin_url": company_linkedin_url,
        "job_title": job_title,
    }


def get_company(job_url: str, html_override: str | None = None) -> dict:
    """Resolve company details from a LinkedIn job URL.

    html_override lets the offline demo feed a saved page instead of hitting the
    network.
    """
    if html_override is not None:
        return parse_company(html_override)

    # Optional: a user-supplied third-party crawler API.
    api_url = os.getenv("LINKEDIN_API_URL")
    if api_url:
        logger.info(">> Using third-party LinkedIn API")
        try:
            resp = httpx.get(api_url, params={"url": job_url}, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if data.get("company_name"):
                return data
        except Exception as e:
            logger.warning("Third-party API failed (%s) — falling back to guest endpoint", e)

    job_id = extract_job_id(job_url)
    if not job_id:
        raise ValueError(f"Could not find a job ID in URL: {job_url}")

    logger.info(">> Fetching LinkedIn guest job posting (id=%s)", job_id)
    resp = httpx.get(
        GUEST_ENDPOINT.format(job_id=job_id),
        headers=HEADERS,
        follow_redirects=True,
        timeout=20,
    )
    resp.raise_for_status()
    return parse_company(resp.text)
