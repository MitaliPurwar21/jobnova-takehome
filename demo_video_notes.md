# Demo recording checklist

Keep it short (~2-3 min). Record the screen + terminal so the stage logs show.

Before recording: set `STAGE1_TIMEOUT = 20` and `STAGE2_TIMEOUT = 45` in
`agent.py` so the timers are quick on camera.

## Run-through

1. Quickly show the files (`agent.py`, `README.md`) and mention keys live in
   `.env.local`.
2. Start `python agent.py console`. Point out `>> STAGE 1: Self-Introduction — STARTED`
   and let it greet me.
3. Give a short intro. When I stop, show the normal transition in the logs:
   `move_to_experience` → `STAGE 2: Past Experience — STARTED`.
4. Answer the experience question + the one follow-up. Let it wrap up.
5. Restart and this time keep talking past 20s to trigger the fallback log:
   `FALLBACK: ... moving to Past Experience`.

## Points to mention

- Two stages with a clean handoff, no repeated prompts.
- Switching logic: tool-based normal transition + timer fallback.
- VAD / turn detection so it doesn't interrupt me.

## Before submitting

- [ ] `.env.local` filled in and working
- [ ] Ran `download-files`
- [ ] Groq TTS terms accepted (so it actually speaks)
- [ ] Timeouts set back if I want defaults
