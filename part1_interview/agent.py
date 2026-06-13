"""Two-stage AI mock interview built with LiveKit Agents.

Stage 1 (IntroAgent) handles the self-introduction, then hands off to
Stage 2 (ExperienceAgent) for past experience. The handoff happens either
when the LLM calls the move_to_experience tool, or via a timer fallback so
the interview always moves forward.
"""

import asyncio
import logging
import os

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ChatContext,
    JobContext,
    RunContext,
    TurnHandlingOptions,
    function_tool,
)
from livekit.plugins import groq, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

load_dotenv(".env.local")

# Seconds before a stage auto-advances if the normal transition never fires.
STAGE1_TIMEOUT = 90   # self-introduction -> past experience
STAGE2_TIMEOUT = 180  # past experience -> wrap up

# Once a fallback is due, wait up to this long for a natural pause before
# switching, so we don't cut the candidate off mid-sentence.
FALLBACK_IDLE_GRACE = 15

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("interview-agent")


async def wait_for_pause(session: AgentSession) -> None:
    """Wait for a gap in the conversation, capped so the interview still moves on."""
    try:
        await asyncio.wait_for(session.wait_for_idle(), timeout=FALLBACK_IDLE_GRACE)
    except Exception:
        pass  # timed out or session busy — proceed anyway


# Prompt used to turn the transcript into a short recap for the hiring team.
SUMMARY_PROMPT = (
    "You are assisting a hiring team. Based only on the interview transcript "
    "below, write a short internal recap. Use exactly this format:\n\n"
    "Takeaways:\n- <point>\n- <point>\n- <point>\n"
    "Feedback for candidate:\n- <one constructive, encouraging suggestion>\n\n"
    "Keep each line short and base everything on what was actually said.\n\n"
    "Transcript:\n"
)


async def print_summary(session: AgentSession) -> None:
    """Generate a short recap from the transcript and log it to the terminal.

    This is the signal an interview is really for — not just the conversation,
    but a quick structured read on the candidate. Demo-level only, not a
    hiring decision.
    """
    transcript = "\n".join(
        f"{'Candidate' if item.role == 'user' else 'Interviewer'}: {item.text_content.strip()}"
        for item in session.history.items
        if getattr(item, "type", None) == "message"
        and item.role in ("user", "assistant")
        and item.text_content
    )
    if not transcript:
        return

    ctx = ChatContext.empty()
    ctx.add_message(role="user", content=SUMMARY_PROMPT + transcript)

    try:
        summary = ""
        stream = session.llm.chat(chat_ctx=ctx)
        async for chunk in stream:
            if chunk.delta and chunk.delta.content:
                summary += chunk.delta.content
        await stream.aclose()
    except Exception as e:
        logger.warning("Could not generate summary: %s", e)
        return

    logger.info(
        "\n========= INTERVIEW SUMMARY =========\n%s\n====================================",
        summary.strip(),
    )


def check_required_env() -> None:
    """Stop with a clear message if any key is missing (values are never printed)."""
    required = [
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "GROQ_API_KEY",
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise SystemExit(
            "\nMissing required environment variable(s): "
            + ", ".join(missing)
            + "\nAdd them to part1_interview/.env.local (see .env.example).\n"
        )


class IntroAgent(Agent):
    """Stage 1 — greets the candidate and runs the self-introduction."""

    def __init__(self) -> None:
        super().__init__(
            instructions="""
            You are a friendly, professional AI interviewer at Jobnova.
            Your only job right now is the self-introduction stage.

            1. Warmly greet the candidate and ask them to introduce themselves.
            2. Ask at most one short follow-up about their background
               (e.g. what they're studying or currently working on).
            3. Once you have a clear picture of who they are, call the
               'move_to_experience' tool to advance to the next stage.
            4. Do not ask about specific past projects or jobs yet, and don't
               repeat your greeting.

            Keep every response short, warm, and natural.
            """,
        )
        self._fallback_task: asyncio.Task | None = None

    async def on_enter(self) -> None:
        logger.info(">> STAGE 1: Self-Introduction — STARTED")

        await self.session.generate_reply(
            instructions=(
                "Warmly welcome the candidate to the Jobnova mock interview. "
                "Briefly mention there are two parts — first a quick "
                "self-introduction, then a chat about their past experience. "
                "Then ask them to start by introducing themselves."
            )
        )

        self._fallback_task = asyncio.create_task(self._fallback_transition())

    async def _fallback_transition(self) -> None:
        try:
            await asyncio.sleep(STAGE1_TIMEOUT)
            await wait_for_pause(self.session)
        except asyncio.CancelledError:
            return  # normal transition already happened

        if isinstance(self.session.current_agent, IntroAgent):
            logger.warning(
                ">> FALLBACK: %ss elapsed in Stage 1 — moving to Past Experience",
                STAGE1_TIMEOUT,
            )
            # Let ExperienceAgent.on_enter speak the transition so we don't
            # produce a duplicate line here.
            self.session.update_agent(ExperienceAgent())

    async def on_exit(self) -> None:
        if self._fallback_task and not self._fallback_task.done():
            self._fallback_task.cancel()
        logger.info(">> STAGE 1: Self-Introduction — ENDED")

    @function_tool
    async def move_to_experience(self, context: RunContext):
        """Move on once the candidate has finished introducing themselves."""
        logger.info(">> NORMAL TRANSITION: 'move_to_experience' tool called")
        return ExperienceAgent()


class ExperienceAgent(Agent):
    """Stage 2 — asks about one past experience plus a follow-up, then ends."""

    def __init__(self) -> None:
        super().__init__(
            instructions="""
            You are a friendly, professional AI interviewer at Jobnova,
            now in the past-experience stage.

            1. Briefly acknowledge the transition (e.g. "Thanks for that intro!")
               without repeating earlier questions.
            2. Ask about one relevant past project or work experience.
            3. Ask exactly one thoughtful follow-up about that same experience
               (their role, a challenge they solved, what they're proud of).
            4. After the follow-up, thank them warmly, then call the
               'end_interview' tool to close the interview.

            Keep responses concise and ask one question at a time.
            """,
        )
        self._fallback_task: asyncio.Task | None = None

    async def on_enter(self) -> None:
        logger.info(">> STAGE 2: Past Experience — STARTED")

        await self.session.generate_reply(
            instructions=(
                "Briefly thank the candidate for their introduction, then ask "
                "them to tell you about one recent or relevant project or work "
                "experience. Keep it to a single, natural question."
            )
        )

        self._fallback_task = asyncio.create_task(self._fallback_end())

    async def _fallback_end(self) -> None:
        try:
            await asyncio.sleep(STAGE2_TIMEOUT)
            await wait_for_pause(self.session)
        except asyncio.CancelledError:
            return

        if isinstance(self.session.current_agent, ExperienceAgent):
            logger.warning(
                ">> FALLBACK: %ss elapsed in Stage 2 — wrapping up", STAGE2_TIMEOUT
            )
            await self.session.generate_reply(
                instructions=(
                    "Warmly wrap up the interview. Thank the candidate for their "
                    "time and let them know the team will be in touch soon."
                )
            )
            await print_summary(self.session)
            logger.info(">> INTERVIEW COMPLETE")

    async def on_exit(self) -> None:
        if self._fallback_task and not self._fallback_task.done():
            self._fallback_task.cancel()
        logger.info(">> STAGE 2: Past Experience — ENDED")

    @function_tool
    async def end_interview(self, context: RunContext):
        """Close the interview once the candidate has been thanked."""
        await print_summary(self.session)
        logger.info(">> INTERVIEW COMPLETE")
        return None


server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: JobContext):
    logger.info(">> New interview session — connecting...")
    await ctx.connect()

    # STT, LLM and TTS all run on a single Groq key; VAD runs locally.
    session = AgentSession(
        stt=groq.STT(model="whisper-large-v3-turbo"),
        llm=groq.LLM(model="llama-3.3-70b-versatile"),
        tts=groq.TTS(),
        vad=silero.VAD.load(),
        turn_handling=TurnHandlingOptions(turn_detection=MultilingualModel()),
    )

    await session.start(agent=IntroAgent(), room=ctx.room)


if __name__ == "__main__":
    check_required_env()
    agents.cli.run_app(server)
