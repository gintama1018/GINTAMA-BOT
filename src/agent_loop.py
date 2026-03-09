"""
src/agent_loop.py — JARVIS Agent Loop Engine (Phase 1 Core)

The heart of JARVIS. Instead of parsing a command into a single Intent and
executing it once, the agent loop lets Gemini decide WHICH tools to call
and in WHAT ORDER — then executes them, returns results to Gemini, and loops
until the task is fully complete.

Flow example:
  User: "open camera and take my selfie"
    → Gemini sees all available tools (phone_launch, phone_screenshot, ...)
    → Gemini calls: phone_launch(app="camera")
    → TCC executes: ADB opens camera app
    → Result returned to Gemini
    → Gemini calls: phone_screenshot()
    → TCC executes: ADB screenshot saved
    → Result returned to Gemini
    → Gemini responds: "Done! Opened camera and took a screenshot."

Key design decisions:
  - Max 20 tool-call iterations per turn (safety limit)
  - 120-second timeout per agent turn
  - Session history loaded from SQLite (persists across restarts)
  - User memory injected into system prompt (per sender)
  - on_tool_call / on_tool_result callbacks for streaming UI updates
  - Clean error messages, never exposes raw tracebacks to users
"""

import os
import time
from typing import Callable, Optional

MAX_TOOL_ITERATIONS = 20
AGENT_TIMEOUT_SECS = 120

AGENT_SYSTEM_PROMPT = """\
You are JARVIS, a personal AI assistant that controls the user's devices and executes tasks.

You have access to tools that control the user's phone (Android via ADB), local PC, \
remote devices, files, and the web. When the user asks you to do something:

1. Use the available tools to accomplish it step-by-step.
2. Call tools in logical order — wait for each result before deciding the next step.
3. After all tools finish, respond with a brief, clear confirmation of what was done.
4. If a tool returns an error, explain it clearly and suggest how to fix it.
5. Never make up tool results. Only report what the tools actually return.
6. Keep final responses concise — 1-3 sentences unless the user asks for details.

For multi-step tasks like "open camera and take a selfie":
  - First call phone_launch(app="camera")
  - Then call phone_screenshot()
  - Then confirm: "Opened camera and took a screenshot."

You are running on the user's own machine. All tool calls are fully local and private.\
"""


class AgentLoop:
    """
    JARVIS agent loop powered by Gemini native function calling.

    Usage:
        loop = AgentLoop(config, logger, executor=LocalExecutor(...), router=CommandRouter(...))
        response = loop.run("open camera and take my selfie")
        print(response)  # "I opened the camera and took a screenshot."

    The loop is stateless per call but loads session history from SessionManager
    so JARVIS remembers previous conversation context.
    """

    def __init__(
        self,
        config: dict,
        logger,
        executor=None,
        router=None,
        session_manager=None,
    ):
        self.config = config
        self.logger = logger
        self._executor = executor
        self._router = router
        self._session_manager = session_manager
        self._tool_exec = None  # lazy-init ToolExecutor
        self._model = None      # lazy-init Gemini model (with tools)
        self._planner = None    # lazy-init TaskPlanner (plain model, no tools)

    # ---------------------------------------------------------------- #
    # Public interface                                                  #
    # ---------------------------------------------------------------- #

    def is_available(self) -> bool:
        """Return True if Gemini is configured and available."""
        llm_cfg = self.config.get("llm", {})
        if not llm_cfg.get("enabled", False):
            return False
        if llm_cfg.get("backend", "gemini") != "gemini":
            return False
        api_key = os.environ.get("GEMINI_API_KEY") or llm_cfg.get("gemini_api_key", "")
        return bool(api_key)

    def run(
        self,
        user_message: str,
        channel: str = "terminal",
        sender_id: str = "local",
        on_tool_call: Optional[Callable] = None,
        on_tool_result: Optional[Callable] = None,
    ) -> str:
        """
        Run the agent loop for a natural-language user message.

        Args:
            user_message:  Natural language input (e.g. "take a selfie")
            channel:       Channel identifier ("terminal", "telegram", etc.)
            sender_id:     Unique sender ID for session isolation
            on_tool_call:  Optional callback(tool_name: str, args: dict) — called
                           immediately before each tool execution (for live UI updates)
            on_tool_result: Optional callback(tool_name: str, result: dict) — called
                            immediately after each tool execution

        Returns:
            Final text response from Gemini after all tool calls complete.
        """
        # ── Permission / meta-commands (/permit, /revoke, /permissions) ── #
        try:
            from src.permission_registry import handle_permission_command
            perm_response = handle_permission_command(user_message)
            if perm_response is not None:
                return perm_response
        except Exception:
            pass

        model = self._get_model()
        if model is None:
            return (
                "Agent loop unavailable: Gemini model not initialised. "
                "Check that GEMINI_API_KEY is set in your .env file."
            )

        tool_exec = self._get_tool_executor()

        # ── Optional multi-step planning pass ────────────────────────── #
        planning_preamble = ""
        try:
            planner = self._get_planner()
            if planner and planner.should_plan(user_message):
                plan = planner.plan(user_message)
                if not plan.is_empty():
                    planning_preamble = f"\n\n[Plan generated: {plan.summary()}]\n\n"
                    self.logger.debug("AgentLoop: using plan:\n%s", plan.summary())
        except Exception as plan_exc:
            self.logger.warning("AgentLoop: planner error (continuing without plan): %s", plan_exc)

        # ── Load session history ──────────────────────────────────────── #
        session_id = None
        history_for_chat = []
        if self._session_manager:
            session_id = self._session_manager.get_or_create_session(channel, sender_id)
            raw_history = self._session_manager.get_history(session_id, limit=20)
            # Gemini chat history: only "user" and "model" turns
            for msg in raw_history:
                if msg["role"] in ("user", "model"):
                    history_for_chat.append({
                        "role": msg["role"],
                        "parts": [{"text": msg["content"]}],
                    })

        # ── Start chat session ────────────────────────────────────────── #
        try:
            chat = model.start_chat(history=history_for_chat)
        except Exception as exc:
            self.logger.warning(f"AgentLoop: failed to start chat: {exc}")
            return f"Error starting agent: {exc}"

        # ── Send user message → begin loop ────────────────────────────── #
        iteration = 0
        start = time.time()
        tool_calls_log = []
        tool_results_log = []
        response_text = ""

        try:
            response = chat.send_message(user_message + planning_preamble)
        except Exception as exc:
            self.logger.warning(f"AgentLoop: Gemini call failed: {exc}")
            return f"Gemini error: {exc}"

        # ── Agent loop ────────────────────────────────────────────────── #
        while iteration < MAX_TOOL_ITERATIONS:
            if time.time() - start > AGENT_TIMEOUT_SECS:
                response_text = "Still working on it… (timeout reached)"
                break

            # Collect all function_call parts from this response
            function_calls = []
            for part in (response.parts or []):
                try:
                    fc = part.function_call
                    if fc and fc.name:
                        function_calls.append(fc)
                except AttributeError:
                    pass

            if not function_calls:
                # No more tool calls → extract final text → done
                try:
                    response_text = response.text or "Done."
                except Exception:
                    response_text = "Task completed."
                break

            # Execute each tool call and package results
            import google.generativeai as genai

            fn_response_parts = []
            for fc in function_calls:
                tool_name = fc.name
                tool_args = dict(fc.args) if fc.args else {}

                self.logger.debug(f"AgentLoop [{iteration}]: {tool_name}({tool_args})")
                tool_calls_log.append({"tool": tool_name, "args": tool_args})

                if on_tool_call:
                    try:
                        on_tool_call(tool_name, tool_args)
                    except Exception:
                        pass

                # ── Execute the tool ─────────────────────────────── #
                result = tool_exec.execute(tool_name, tool_args)
                tool_results_log.append({"tool": tool_name, "result": result})

                if on_tool_result:
                    try:
                        on_tool_result(tool_name, result)
                    except Exception:
                        pass

                self.logger.debug(f"AgentLoop: {tool_name} → {result}")

                # Package as Gemini FunctionResponse part
                fn_response_parts.append(
                    genai.protos.Part(
                        function_response=genai.protos.FunctionResponse(
                            name=tool_name,
                            response=(
                                result
                                if isinstance(result, dict)
                                else {"result": str(result)}
                            ),
                        )
                    )
                )

            # Send all function results back to Gemini
            try:
                response = chat.send_message(fn_response_parts)
            except Exception as exc:
                self.logger.warning(f"AgentLoop: error sending tool results: {exc}")
                response_text = f"Tools executed but error getting Gemini response: {exc}"
                break

            iteration += 1

        else:
            # Exceeded MAX_TOOL_ITERATIONS
            response_text = (
                f"Completed {MAX_TOOL_ITERATIONS} tool calls. "
                "Task may have additional steps — ask again to continue."
            )

        # ── Persist to session history ────────────────────────────────── #
        if self._session_manager and session_id:
            self._session_manager.add_message(session_id, "user", user_message)
            self._session_manager.add_message(
                session_id,
                "model",
                response_text,
                tool_calls=tool_calls_log or None,
                tool_results=tool_results_log or None,
            )

        return response_text or "Done."

    # ---------------------------------------------------------------- #
    # Lazy initialisation                                               #
    # ---------------------------------------------------------------- #

    def _get_tool_executor(self):
        if self._tool_exec is None:
            from src.tool_registry import ToolExecutor
            self._tool_exec = ToolExecutor(
                self.config, self.logger, self._executor, self._router
            )
        return self._tool_exec

    def _get_planner(self):
        """Return a TaskPlanner (created once and cached)."""
        if self._planner is None:
            try:
                from src.planner import make_planner
                from src.tool_registry import get_declarations
                tool_names = [td["name"] for td in get_declarations()]
                self._planner = make_planner(self.config, tool_names)
            except Exception as exc:
                self.logger.warning("AgentLoop: could not init planner: %s", exc)
                self._planner = False  # sentinel: don't retry
        return self._planner if self._planner else None

    def _get_model(self):
        """Initialise Gemini model with all tool declarations. Cached after first call."""
        if self._model is not None:
            return self._model

        try:
            import google.generativeai as genai
            from google.generativeai.types import FunctionDeclaration, Tool
        except ImportError:
            self.logger.warning(
                "google-generativeai not installed. "
                "Run: pip install google-generativeai"
            )
            return None

        llm_cfg = self.config.get("llm", {})
        api_key = os.environ.get("GEMINI_API_KEY") or llm_cfg.get("gemini_api_key", "")
        if not api_key:
            self.logger.warning("AgentLoop: GEMINI_API_KEY not set")
            return None

        try:
            genai.configure(api_key=api_key)

            model_name = llm_cfg.get("gemini_model", "gemini-2.5-flash")

            from src.tool_registry import get_declarations
            fn_declarations = [
                FunctionDeclaration(
                    name=td["name"],
                    description=td["description"],
                    parameters=td["parameters"],
                )
                for td in get_declarations()
            ]

            tool = Tool(function_declarations=fn_declarations)

            self._model = genai.GenerativeModel(
                model_name=model_name,
                tools=[tool],
                system_instruction=AGENT_SYSTEM_PROMPT,
                generation_config={
                    "max_output_tokens": 1024,
                    "temperature": 0.1,
                },
            )
            self.logger.debug(f"AgentLoop: Gemini model '{model_name}' ready with {len(fn_declarations)} tools")
            return self._model

        except Exception as exc:
            self.logger.warning(f"AgentLoop: failed to init Gemini model: {exc}")
            return None
