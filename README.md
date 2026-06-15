# Jobnova Take-Home — Part 1: AI Mock Interview

My take on Part 1: a voice-based mock interview agent built with
[LiveKit Agents](https://github.com/livekit/agents). It runs as a few agents
that hand off to each other and move the interview along on their own.

- **Stage 1 — Self-Introduction** (`IntroAgent`): greets the candidate and asks
  them to introduce themselves.
- **Stage 2 — Past Experience** (`ExperienceAgent`): asks about one past
  project/experience and a single follow-up.
- **Stage 3 — Wrap-Up & Questions** (`ClosingAgent`): invites the candidate's
  own questions, answers them, then closes the interview.

When it closes, it also prints a short **interview summary** to the terminal: a
few takeaways plus one piece of feedback, generated from the actual transcript.
(It's a demo-level recap, not a hiring decision.)

### How the stage switching works

Each stage is a LiveKit `Agent`, and the progression is driven in code (in
`on_user_turn_completed`) rather than left to the LLM, so the model only phrases
the questions and can't stack them or jump stages:

- **Normal transition:** once the candidate finishes their introduction,
  `IntroAgent` hands off to `ExperienceAgent`. After one experience answer and a
  follow-up, that hands off to `ClosingAgent`. The closing stage is open-ended —
  the candidate can ask questions and the agent answers — and it ends when the
  candidate has nothing more to ask (the LLM calls a small `end_interview` tool).
- **Timer fallback:** if the candidate goes quiet, a background timer per stage
  keeps things moving — Stage 1 advances after `STAGE1_TIMEOUT` (90s), Stage 2
  wraps up after `STAGE2_TIMEOUT` (180s), and Stage 3 closes after
  `STAGE3_TIMEOUT` (60s). Each waits for a natural pause first so it doesn't cut
  the candidate off.
- **No overlap / no repeats:** only one stage speaks at a time, interruptions
  are disabled while the agent talks (so it doesn't react to its own voice on
  open speakers), and Silero VAD + the turn detector handle end-of-turn.

Every transition is logged to the terminal, e.g. `>> STAGE 2: Past Experience — STARTED`.

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

Needs Python 3.9+ (I used 3.13), a mic + speakers, a LiveKit Cloud project, and
two free API keys: Groq (the interviewer LLM) and Deepgram (speech).

```bash
cd part1_interview
python3 -m venv venv
source venv/bin/activate          # Windows: .\venv\Scripts\activate
pip install -r requirements.txt
```

Copy the env template and fill in your values:

```bash
cp .env.example .env.local
```

```
LIVEKIT_URL=wss://<your-project>.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
GROQ_API_KEY=...
DEEPGRAM_API_KEY=...
```

- LiveKit values: LiveKit Cloud dashboard, Settings → Keys.
- Groq key (the LLM): https://console.groq.com/keys.
- Deepgram key (speech-to-text and the agent's voice): https://console.deepgram.com/.
  I use Deepgram for speech because Groq's free TTS is capped per day.

Then download the local speech models once:

```bash
python agent.py download-files
```

## Running it

Console mode is easiest — it uses your own mic and speakers, no browser:

```bash
python agent.py console
```

To run against LiveKit Cloud and join from the
[Agents Playground](https://agents-playground.livekit.io/):

```bash
python agent.py dev
```

## Testing the flow

1. Start `python agent.py console`. You should hear the greeting and see
   `>> STAGE 1: Self-Introduction — STARTED`.
2. Give a short intro. When you finish, it logs
   `>> NORMAL TRANSITION: intro complete — moving to Past Experience` and then
   `>> STAGE 2: Past Experience — STARTED`.
3. Answer the experience question and the follow-up. It moves to
   `>> STAGE 3: Wrap-Up & Questions — STARTED` and asks if you have any questions.
4. Ask it something (e.g. "what's the team like?") and it answers. When you say
   you have no more questions, it closes, prints the
   `========= INTERVIEW SUMMARY =========` block, and logs `>> INTERVIEW COMPLETE`.
5. To see a fallback in action, lower a timeout (e.g. `STAGE1_TIMEOUT = 20`) at
   the top of `agent.py`, stay quiet past it, and watch for the
   `>> FALLBACK: ...` log.

## Config

`STAGE1_TIMEOUT`, `STAGE2_TIMEOUT`, and `STAGE3_TIMEOUT` (top of `agent.py`)
control the fallback timing. The voice and model can be changed in
`groq.LLM(...)` and `deepgram.TTS(...)`.
