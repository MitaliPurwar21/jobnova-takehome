# Jobnova Take-Home — Part 2: AI Job Source Agent

My take on Part 2: given a LinkedIn job listing URL, the agent figures out the
company, finds its careers page, and returns one open position on it — then
verifies the links resolve.

```
LinkedIn job URL
   └─ SIG  company name + website
        └─ NAV  company website → careers page
             └─ EXT  careers page → one open position (deep link)
                  └─ VER  confirm the URLs resolve
Output: { company_name, company_website, career_page_url, open_position_url }
```

It runs **for free with no API keys at all**. Every step has a keyless default,
and the only optional key (Groq) just makes the navigation smarter. There's a
CLI and a small live web UI.

## How it works

- **SIG — company (`linkedin.py`, `company.py`).** LinkedIn blocks logged-out
  scraping of the normal job page, but its public `jobs-guest` posting endpoint
  returns the same job card without a login, which is enough to read the
  company name. The name is then turned into a website with Clearbit's keyless
  autocomplete endpoint (DuckDuckGo as a fallback).
- **NAV / EXT — the web agent (`web_agent.py`).** Starting from the company
  homepage, it collects the links on the page and decides which to follow toward
  the careers page, then does the same to find a posting. Big-company homepages
  often render their menus in JavaScript, so if no careers link shows up in the
  HTML it falls back to probing common paths (`/careers`, `/jobs`,
  `careers.<domain>`, …) and keeps the first that actually resolves. Careers
  pages usually hand off to an applicant-tracking board (Lever, Greenhouse,
  Ashby, Workday…), which is itself just a list, so it follows a couple of hops
  until it lands on a single posting URL. When a careers page has no postings in
  its static HTML, it retries with a headless browser and also harvests posting
  URLs from the page scripts — so widget-based boards (e.g. hireclick) that
  inject their listings with JavaScript still resolve to a real posting.
- **VER — verification (`verify.py`).** The career and position URLs are
  HEAD-checked in parallel, so a returned link is confirmed reachable rather than
  just plausible.
- **The "decide which link" part** uses a Groq LLM when `GROQ_API_KEY` is set.
  If it isn't, it falls back to keyword heuristics, so the agent still runs with
  no key. (I used Groq because it has a free tier and I already had a key from
  Part 1. Swapping in another model is a one-method change in `web_agent.py`.)

## Setup

```bash
cd part2_job_source
python3 -m venv venv
source venv/bin/activate          # Windows: .\venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium       # one-time; used to read JS-rendered careers pages
```

Optional — copy the env template if you want the LLM-driven navigation:

```bash
cp .env.example .env              # then add your free Groq key
```

## Running it — CLI

Offline demo (saved LinkedIn job card + live company lookups):

```bash
python job_source_agent.py --demo
```

Against a real LinkedIn job URL (or just a company name):

```bash
python job_source_agent.py "https://www.linkedin.com/jobs/view/4427958773"
python job_source_agent.py "Figma"
```

The result is printed and saved to `output/result.json`:

```json
{
  "company_name": "iCatalyst, Inc.",
  "company_website": "https://icatalystinc.com",
  "career_page_url": "https://icatalystinc.com/careers.html",
  "open_position_url": "https://icatalystinc.hireclick.com/jb/sr-ai-developer/view/246353",
  "open_positions": [
    {"title": "Sr Ai Developer", "url": "https://icatalystinc.hireclick.com/jb/sr-ai-developer/view/246353"},
    {"title": "Hr Recruiting Intern", "url": "https://icatalystinc.hireclick.com/jb/hr-recruiting-intern/view/247319"},
    {"title": "Ai Developer Intern", "url": "https://icatalystinc.hireclick.com/jb/ai-developer-intern/view/247451"}
  ],
  "verified": {
    "company_website": true,
    "career_page_url": true,
    "open_position_url": true
  }
}
```

`open_position_url` is the single deliverable the challenge asks for (the first
posting found); `open_positions` lists every posting the agent found, for
convenience. When a board exposes its titles only via JavaScript (e.g. Lever),
the URL is still returned and the title may be `null`.

## Running it — web UI

```bash
python server.py        # then open http://localhost:5001
```

Paste a LinkedIn job URL (or a company name) and hit RUN. The four stages stream
their progress live, and the final card shows the deliverable line.

### JS-rendered pages

Most company sites are plain enough to read over HTTP. When a careers page has
no postings in its static HTML, the agent automatically retries it with a
headless browser (Playwright), which covers widget-based boards that inject
listings with JavaScript. You can also force the browser path on every fetch
with `--render` (CLI). If Playwright/Chromium isn't installed, it falls back to
the static result instead of failing.

## Project structure

```
part2_job_source/
├── job_source_agent.py   # CLI + pipeline orchestration
├── server.py             # optional Flask/SSE web UI
├── templates/index.html  # web UI front end
├── linkedin.py           # SIG: company name from a LinkedIn job URL
├── company.py            # SIG: company name → website
├── web_agent.py          # NAV/EXT: website → careers → one posting
├── verify.py             # VER: parallel URL reachability checks
├── fetcher.py            # HTTP fetch (+ optional headless-browser fallback)
├── fixtures/             # saved LinkedIn job card for --demo
├── requirements.txt
└── .env.example
```

## Notes / limitations

- Tested across several applicant-tracking systems (Lever, Greenhouse, Ashby,
  hireclick) and large self-hosted sites (IBM, Google, VMware).
- A few big companies (e.g. Google, IBM, VMware) run their careers page as a
  JavaScript search app that loads jobs from a private API behind a search box.
  For those the careers URL is found and verified, but the individual posting
  comes back `null` (with an explanatory note) rather than a guessed link.
- LinkedIn's guest endpoint can rate-limit if hit a lot; for heavy use you can
  plug a third-party crawler API in via `LINKEDIN_API_URL` in `.env`.
- Company-website resolution is best-effort — for a very generic company name,
  Clearbit may return a close-but-wrong domain.
- If a step can't be completed, that field comes back `null` and the CLI exits
  non-zero, so it's obvious which hop failed.
