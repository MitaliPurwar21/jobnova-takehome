# Jobnova AI Engineer — Take-Home

My submission for the Jobnova take-home. I implemented Part 1 and Part 2; each
lives in its own folder with its own README, setup, and run instructions.

- **[Part 1 — AI Mock Interview](part1_interview/README.md)** (`part1_interview/`)
  A voice mock interview built on LiveKit Agents: self-introduction → past
  experience → wrap-up. Stage transitions are driven in code with per-stage
  timer fallbacks, and it prints a short interview summary at the end.

- **[Part 2 — AI Job Source Agent](part2_job_source/README.md)** (`part2_job_source/`)
  From a LinkedIn job URL it finds the company, its careers page, and an open
  position, verifying the links. Runs free with no API keys; comes with a CLI
  and a small web UI.

## Structure

```
jobnova-challenge/
├── part1_interview/    # Part 1 — LiveKit mock interview
└── part2_job_source/   # Part 2 — job source agent
```

See each part's README for how to set it up and run it.
