"""Multi-stage AI mock interview built with LiveKit Agents.

The interview runs as three agents that hand off to each other:
  Stage 1 (IntroAgent)      - self-introduction
  Stage 2 (ExperienceAgent) - one past experience + a follow-up
  Stage 3 (ClosingAgent)    - answers the candidate's own questions, then closes

Stage progression is driven in code so the interview always moves forward, with
a timer fallback per stage for when the candidate goes quiet.
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
    EndpointingOptions,
    InterruptionOptions,
    JobContext,
    RunContext,
    StopResponse,
    TurnHandlingOptions,
    function_tool,
)
from livekit.agents.llm import ChatMessage
from livekit.plugins import deepgram, groq, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

load_dotenv(".env.local")

# Seconds before a stage auto-advances if the normal transition never fires.
STAGE1_TIMEOUT = 90    # self-introduction -> past experience
STAGE2_TIMEOUT = 180   # past experience -> wrap up
STAGE3_TIMEOUT = 60    # Q&A -> close when the candidate has nothing more to ask

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
        pass


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
    """Generate a short recap from the transcript and log it to the terminal."""
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


async def close_interview(session: AgentSession) -> None:
    """Speak a closing line, print the summary, and log completion.

    Used by the timer fallbacks, where the candidate has gone quiet and the
    agent still needs to sign off on its own.
    """
    await session.generate_reply(
        instructions=(
            "Warmly thank the candidate for their time, clearly let them know "
            "that this is the end of the interview, and that the team will be in "
            "touch soon. Keep it to a short, friendly sign-off."
        )
    )
    await print_summary(session)
    logger.info(">> INTERVIEW COMPLETE")


def check_required_env() -> None:
    """Stop with a clear message if any key is missing (values are never printed)."""
    required = [
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "GROQ_API_KEY",      # the interviewer LLM
        "DEEPGRAM_API_KEY",  # speech-to-text + text-to-speech
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise SystemExit(
            "\nMissing required environment variable(s): "
            + ", ".join(missing)
            + "\nAdd them to part1_interview/.env.local (see .env.example).\n"
        )


class IntroAgent(Agent):
    """Stage 1 - greets the candidate and runs the self-introduction."""

    def __init__(self) -> None:
        super().__init__(
            instructions="""
            You are a friendly, professional AI interviewer at Jobnova.
            You handle the self-introduction stage only: warmly greet the
            candidate and ask them to introduce themselves. Keep it short and
            natural. Ask only for their introduction, no follow-ups and nothing
            about past experience; the next stage covers that.
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
            # Let ExperienceAgent.on_enter speak the transition line.
            self.session.update_agent(ExperienceAgent())

    async def on_exit(self) -> None:
        if self._fallback_task and not self._fallback_task.done():
            self._fallback_task.cancel()
        logger.info(">> STAGE 1: Self-Introduction — ENDED")

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        # Advance in code once the candidate has introduced themselves, so the
        # intro is always exactly one question and the next stage's greeting
        # can't overlap with a stray follow-up.
        logger.info(">> NORMAL TRANSITION: intro complete — moving to Past Experience")
        self.session.update_agent(ExperienceAgent())
        raise StopResponse()


class ExperienceAgent(Agent):
    """Stage 2 - asks about one past experience plus a follow-up."""

    def __init__(self) -> None:
        super().__init__(
            instructions="""
            You are a friendly, professional AI interviewer at Jobnova in the
            past-experience stage. Ask concise, natural questions about the
            candidate's past work, one at a time, and never ask more than one
            question in a single turn.
            """,
        )
        self._answers = 0
        self._done = False
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

        if isinstance(self.session.current_agent, ExperienceAgent) and not self._done:
            logger.warning(
                ">> FALLBACK: %ss elapsed in Stage 2 — wrapping up", STAGE2_TIMEOUT
            )
            self._done = True
            await close_interview(self.session)

    async def on_exit(self) -> None:
        if self._fallback_task and not self._fallback_task.done():
            self._fallback_task.cancel()
        logger.info(">> STAGE 2: Past Experience — ENDED")

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        # Drive the stage in code: first answer -> one follow-up; second answer
        # -> hand off to the closing stage. The LLM only phrases the questions,
        # so it can't stack questions or end early.
        if self._done:
            raise StopResponse()

        self._answers += 1
        if self._answers == 1:
            await self.session.generate_reply(
                instructions=(
                    "Ask one thoughtful follow-up about that same experience — "
                    "their specific role, a challenge they solved, or what they "
                    "are most proud of. Ask a single question."
                )
            )
        else:
            self._done = True
            self.session.update_agent(ClosingAgent())
        raise StopResponse()


class ClosingAgent(Agent):
    """Stage 3 - invites the candidate's own questions, answers them, then closes."""

    def __init__(self) -> None:
        super().__init__(
            instructions="""
            You are a friendly, professional AI interviewer at Jobnova wrapping
            up the interview. Answer the candidate's questions about the role,
            the team, or the company briefly and warmly. If you don't know a
            specific detail, say the team can follow up after the interview.
            When the candidate has no more questions, just call the
            'end_interview' tool — it speaks the closing sign-off for you, so
            don't say goodbye yourself.
            """,
        )
        self._closed = False
        self._fallback_task: asyncio.Task | None = None

    async def on_enter(self) -> None:
        logger.info(">> STAGE 3: Wrap-Up & Questions — STARTED")

        await self.session.generate_reply(
            instructions=(
                "Briefly thank the candidate for sharing their experience, then "
                "ask if they have any questions for you about the role or what "
                "it's like working at Jobnova. Ask a single question."
            )
        )

        self._fallback_task = asyncio.create_task(self._fallback_end())

    async def _fallback_end(self) -> None:
        try:
            await asyncio.sleep(STAGE3_TIMEOUT)
            await wait_for_pause(self.session)
        except asyncio.CancelledError:
            return

        if isinstance(self.session.current_agent, ClosingAgent) and not self._closed:
            logger.warning(
                ">> FALLBACK: %ss elapsed in Stage 3 — closing", STAGE3_TIMEOUT
            )
            self._closed = True
            await close_interview(self.session)

    async def on_exit(self) -> None:
        if self._fallback_task and not self._fallback_task.done():
            self._fallback_task.cancel()
        logger.info(">> STAGE 3: Wrap-Up & Questions — ENDED")

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        # No StopResponse here on purpose: let the LLM actually answer the
        # candidate's questions so the conversation stays two-way until they're
        # done. Once closed, ignore any further input.
        if self._closed:
            raise StopResponse()

    @function_tool
    async def end_interview(self, context: RunContext):
        """Close the interview once the candidate has no more questions."""
        if self._closed:
            return None
        self._closed = True
        if self._fallback_task and not self._fallback_task.done():
            self._fallback_task.cancel()
        logger.info(">> NORMAL TRANSITION: no more questions — closing interview")
        # close_interview speaks the sign-off; calling generate_reply from inside
        # a tool is the supported way to make it the agent's final spoken turn.
        await close_interview(self.session)
        return None


server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: JobContext):
    logger.info(">> New interview session — connecting...")
    await ctx.connect()

    # Deepgram handles speech (STT + TTS); Groq runs the LLM; VAD is local.
    # (Groq's free TTS is capped per day, so the voice goes through Deepgram.)
    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=groq.LLM(model="llama-3.3-70b-versatile"),
        tts=deepgram.TTS(model="aura-2-andromeda-en"),
        # Higher activation threshold so speaker echo doesn't trigger the mic —
        # lets the demo run without headphones.
        vad=silero.VAD.load(activation_threshold=0.75, min_silence_duration=0.6),
        turn_handling=TurnHandlingOptions(
            turn_detection=MultilingualModel(),
            # Wait a moment after the candidate stops before replying, so natural
            # mid-answer pauses don't get cut off (streaming STT ends eagerly).
            endpointing=EndpointingOptions(min_delay=1.0, max_delay=6.0),
            # Don't let the agent be interrupted while speaking, and drop mic
            # audio captured during that time, so its own voice isn't
            # transcribed as the candidate answering (which caused back-to-back
            # questions on open speakers).
            interruption=InterruptionOptions(
                enabled=False,
                discard_audio_if_uninterruptible=True,
            ),
        ),
    )

    await session.start(agent=IntroAgent(), room=ctx.room)


if __name__ == "__main__":
    check_required_env()
    agents.cli.run_app(server)
