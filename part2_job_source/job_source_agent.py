"""AI Job Source agent — Jobnova take-home, Part 2.

Pipeline, starting from a LinkedIn job listing URL:
  1. company name + website   (linkedin.py / company.py)
  2. company website -> career page URL   (web_agent.py)
  3. career page -> one open position URL (web_agent.py)

Output: {company_name, career_page_url, open_position_url}, printed and saved
to output/result.json.

Usage:
  python job_source_agent.py <linkedin_job_url>
  python job_source_agent.py --demo        # offline run using the saved sample
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

import company
import linkedin
from verify import verify_urls
from web_agent import LLM, find_career_page, find_open_positions

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # quiet per-request noise
logger = logging.getLogger("job-source")

OUTPUT_FILE = Path(__file__).parent / "output" / "result.json"
SAMPLE_FILE = Path(__file__).parent / "fixtures" / "linkedin_job_sample.html"


def resolve_company_name(source: str | None, demo: bool) -> str:
    """Get the company name from a LinkedIn URL, a saved sample, or a raw name."""
    if demo:
        logger.info(">> DEMO MODE: reading saved LinkedIn sample (no network for step 1)")
        info = linkedin.get_company("", html_override=SAMPLE_FILE.read_text())
    elif source and "linkedin.com" in source:
        logger.info(">> STEP 1: Reading company from LinkedIn job page")
        info = linkedin.get_company(source)
    else:
        # Plain company name — skip the LinkedIn crawl.
        return (source or "").strip()

    name = info.get("company_name")
    if not name:
        raise ValueError("Could not determine the company name from the LinkedIn page.")
    return name


def result_note(website: str | None, career_url: str | None, position_url: str | None) -> str | None:
    """A short human explanation of the outcome, especially when a step is null."""
    if not website:
        return "Could not resolve the company's website."
    if not career_url:
        return "Found the company, but could not locate a careers page."
    if not position_url:
        return (
            "Careers page found, but no link to an individual posting was detected — "
            "the roles are listed on the careers page itself, or loaded by a script, "
            "rather than linked separately. Open the careers page above to see the "
            "current openings."
        )
    return None


def run(source: str | None, demo: bool, render: bool) -> dict:
    llm = LLM()
    logger.info(
        ">> Web agent reasoning: %s",
        "Groq LLM" if llm.available else "heuristics (no GROQ_API_KEY set)",
    )

    company_name = resolve_company_name(source, demo)
    logger.info(">> Company: %s", company_name)

    website = company.resolve_website(company_name)
    career_url = find_career_page(website, llm, render=render) if website else None
    positions = find_open_positions(career_url, llm, render=render) if career_url else []
    position_url = positions[0]["url"] if positions else None

    logger.info(">> Verifying result URLs")
    checks = verify_urls([website, career_url, position_url])

    def status(url: str | None) -> bool | None:
        # None when there's no URL to check, so it reads as "n/a" not "failed".
        return checks.get(url, False) if url else None

    return {
        "company_name": company_name,
        "company_website": website,
        "career_page_url": career_url,
        "open_position_url": position_url,  # the single deliverable the spec asks for
        "open_positions": positions,        # everything we found, for convenience
        "verified": {
            "company_website": status(website),
            "career_page_url": status(career_url),
            "open_position_url": status(position_url),
        },
        "note": result_note(website, career_url, position_url),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Job Source agent (Jobnova Part 2)")
    parser.add_argument("source", nargs="?", help="LinkedIn job URL or a company name")
    parser.add_argument(
        "--demo", action="store_true", help="Run offline using the saved sample job page"
    )
    parser.add_argument(
        "--render",
        action="store_true",
        help="Use a headless browser for company pages (needs `playwright install chromium`)",
    )
    args = parser.parse_args()

    if not args.source and not args.demo:
        parser.error("provide a LinkedIn job URL or company name, or use --demo")

    result = run(args.source, args.demo, args.render)

    OUTPUT_FILE.parent.mkdir(exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(result, indent=2))

    print("\n========= RESULT =========")
    print(json.dumps(result, indent=2))
    print("==========================")
    if result.get("note"):
        logger.info(">> NOTE: %s", result["note"])
    logger.info(">> Saved to %s", OUTPUT_FILE)

    if not result["open_position_url"]:
        sys.exit(2)  # pipeline ran but couldn't complete every step


if __name__ == "__main__":
    main()
