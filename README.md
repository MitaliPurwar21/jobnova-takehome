# Jobnova Take-Home — Part 1: AI Mock Interview

My implementation of Part 1: a voice-based mock interview agent built with
[LiveKit Agents](https://github.com/livekit/agents). It runs two stages and
moves between them on its own.

- **Stage 1 — Self-Introduction** (`IntroAgent`): greets the candidate and asks
  them to introduce themselves, with at most one short follow-up.
- **Stage 2 — Past Experience** (`ExperienceAgent`): asks about one past
  project/experience and one follow-up, then ends politely.

At the end it also prints a short **interview summary** to the terminal — a few
takeaways plus one piece of feedback, generated from the actual transcript. The
point of an interview agent isn't just the chat; it's producing usable signal on
the candidate. (This is a demo-level recap, not a hiring decision — a real
product would need fairness/bias review before any of it counts.)

### How the stage switching works

I used LiveKit's multi-agent handoff pattern:

- **Normal transition:** `IntroAgent` has a `move_to_experience` tool. When the
  model decides the intro is done, it calls the tool and we hand off to
  `ExperienceAgent`.
- **Timer fallback:** if the tool never fires, a background timer forces the
  transition after `STAGE1_TIMEOUT` (90s). Stage 2 has a similar timer that
  wraps the interview up after `STAGE2_TIMEOUT` (180s).
- To avoid repeating prompts, the handoff just returns the next agent and lets
  that agent's `on_enter` speak the transition line. Silero VAD + the turn
  detector handle end-of-turn so the agent doesn't talk over the candidate.

Each transition is logged to the terminal, e.g. `>> STAGE 2: Past Experience — STARTED`.

## Project structure

```
jobnova-challenge/
├── README.md
└── part1_interview/
    ├── agent.py
    ├── requirements.txt
    └── .env.example
```

## Setup

Requires Python 3.9+ (tested on 3.13), a mic + speakers, a LiveKit Cloud
project, and a free Groq API key.

```bash
cd part1_interview
python3 -m venv venv
source venv/bin/activate          # Windows: .\venv\Scripts\activate
pip install -r requirements.txt
```

Create your env file from the template and fill in the values:

```bash
cp .env.example .env.local
```

```
LIVEKIT_URL=wss://<your-project>.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
GROQ_API_KEY=...
```

- LiveKit values: from the LiveKit Cloud dashboard (Settings → Keys).
- Groq key: from https://console.groq.com/keys. One key covers STT, the LLM,
  and TTS.

Then download the local speech models once:

```bash
python agent.py download-files
```

> Note: Groq's TTS model needs a one-time terms acceptance. If the agent thinks
> but won't speak, open
> https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english
> and accept the terms.

## Running it

Easiest is console mode — it uses your own mic and speakers, no browser needed:

```bash
python agent.py console
```

To run against LiveKit Cloud instead and join from the
[Agents Playground](https://agents-playground.livekit.io/):

```bash
python agent.py dev
```

## Testing the flow

1. Start `python agent.py console`. You should hear the greeting and see
   `>> STAGE 1: Self-Introduction — STARTED`.
2. Give a short intro. When you finish, the agent moves on and logs
   `>> NORMAL TRANSITION: 'move_to_experience' tool called` →
   `>> STAGE 2: Past Experience — STARTED`.
3. Answer the experience question and the follow-up; the agent wraps up and
   prints the `========= INTERVIEW SUMMARY =========` block, then
   `>> INTERVIEW COMPLETE`.
4. To see the fallback, lower `STAGE1_TIMEOUT` (e.g. to 20) at the top of
   `agent.py` and keep talking past it — you'll see
   `>> FALLBACK: ... moving to Past Experience`.

## Config

`STAGE1_TIMEOUT` and `STAGE2_TIMEOUT` (top of `agent.py`) control the fallback
timing. Voice/model can be changed in `groq.LLM(...)` and `groq.TTS(...)`.
