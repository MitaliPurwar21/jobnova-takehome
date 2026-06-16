"""Optional web UI for the job source agent.

Streams the pipeline live over Server-Sent Events so each stage shows up as it
runs. The actual work is the same backend the CLI uses (linkedin / company /
web_agent / verify) — this is just a nicer way to watch it for the demo.

Run:  python server.py   ->   http://localhost:5001
"""

import json
import logging

from dotenv import load_dotenv
from flask import Flask, Response, render_template, request

import company
from job_source_agent import resolve_company_name, result_note
from verify import verify_url, verify_urls
from web_agent import LLM, find_career_page, find_open_positions

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

app = Flask(__name__)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/run")
def run():
    source = request.args.get("input", "").strip()
    render = request.args.get("render") == "1"

    def stream():
        if not source:
            yield _sse("failure", {"message": "empty input"})
            return
        llm = LLM()
        try:
            # SIG-01 — company name + website
            yield _sse("transmitting", {"agent": "SIG-01"})
            name = resolve_company_name(source, demo=False)
            website = company.resolve_website(name)
            yield _sse(
                "locked",
                {
                    "agent": "SIG-01",
                    "output": {"company_name": name, "company_website": website},
                    "verified": verify_url(website),
                    "engine": "Groq LLM" if llm.available else "heuristics",
                },
            )

            # NAV-02 — careers page
            yield _sse("transmitting", {"agent": "NAV-02"})
            career = find_career_page(website, llm, render=render) if website else None
            yield _sse("locked", {"agent": "NAV-02", "output": {"career_page_url": career}})

            # EXT-03 — open positions
            yield _sse("transmitting", {"agent": "EXT-03"})
            positions = find_open_positions(career, llm, render=render) if career else []
            position = positions[0]["url"] if positions else None
            yield _sse(
                "locked",
                {
                    "agent": "EXT-03",
                    "output": {"open_position_url": position},
                    "positions": positions,
                    "note": None if position else result_note(website, career, position),
                },
            )

            # VER-04 — confirm the result URLs resolve
            yield _sse("transmitting", {"agent": "VER-04"})
            checks = verify_urls([career, position])
            yield _sse(
                "locked",
                {
                    "agent": "VER-04",
                    "output": {
                        "career_page_url": checks.get(career, False) if career else None,
                        "open_position_url": checks.get(position, False) if position else None,
                    },
                },
            )

            yield _sse(
                "done",
                {
                    "company_name": name,
                    "company_website": website,
                    "career_page_url": career,
                    "open_position_url": position,
                    "open_positions": positions,
                    "spec_format": f"{name},{career or ''},{position or ''}",
                    "note": result_note(website, career, position),
                },
            )
        except Exception as e:
            yield _sse("failure", {"message": str(e)})

    return Response(stream(), mimetype="text/event-stream")


if __name__ == "__main__":
    print("\nJobnova Job Source Agent — http://localhost:5001\n")
    app.run(port=5001, threaded=True)
