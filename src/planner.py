"""
src/planner.py — Multi-step task planner for JARVIS.

Upgrades the agent from reactive (AI → tools) to deliberative (AI → plan → tools → memory).

The planner uses Gemini to decompose a user request into an ordered list of
tool-call steps BEFORE the agent loop executes them.  This enables:

  - Longer-horizon tasks spanning many tool calls
  - Dependency ordering ("read calendar BEFORE generating notes")
  - Transparent reasoning the user can inspect

How it integrates:
    1. AgentLoop.run() asks the Planner: "should this task be planned?"
    2. Planner sends a lightweight Gemini call to generate a step list
    3. AgentLoop executes each step in order and reports progress
    4. Results from each step feed into the prompt of the next step

Example:
    User: "prepare meeting notes"

    Planner generates:
        Step 1: web_search(query="today's meeting agenda")
        Step 2: file_read(path="~/Documents/meeting_topics.txt")
        Step 3: (generate summary — text only, no tool)
        Step 4: system_notify(message="Meeting notes ready")

Usage:
    from src.planner import TaskPlanner
    planner = TaskPlanner(llm_adapter)
    plan = planner.plan("prepare meeting notes")
    for step in plan:
        print(step.tool, step.args)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("jarvis.planner")

# ─────────────────────────────────────────────────────────────────────────── #
#  Data structures                                                             #
# ─────────────────────────────────────────────────────────────────────────── #

@dataclass
class PlanStep:
    step: int
    tool: Optional[str]           # None = "think / summarise" step (no tool call)
    args: dict = field(default_factory=dict)
    description: str = ""         # human-readable why
    depends_on: list[int] = field(default_factory=list)  # step numbers this waits for


@dataclass
class TaskPlan:
    task: str
    steps: list[PlanStep]

    def is_empty(self) -> bool:
        return len(self.steps) == 0

    def summary(self) -> str:
        lines = [f"Plan for: {self.task!r}"]
        for s in self.steps:
            tool_str = f"{s.tool}({s.args})" if s.tool else "(think)"
            lines.append(f"  Step {s.step}: {tool_str} — {s.description}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────── #
#  Prompt                                                                      #
# ─────────────────────────────────────────────────────────────────────────── #

_PLAN_SYSTEM = """\
You are the planning module of JARVIS, a personal AI automation system.

When given a multi-step task, decompose it into an ordered sequence of concrete actions.
Each action either calls a specific JARVIS tool or is a reasoning/summary step.

Available tools: {tools}

Rules:
- Only use tools from the available list above
- Use null for "tool" if a step is pure reasoning (e.g., "summarise results")
- Steps execute in order; use depends_on to express dependencies
- Prefer the MINIMUM number of steps — avoid padding
- If the task needs only ONE tool call, return a single-step plan
- For simple conversational queries, return an empty array []

Respond with ONLY valid JSON, no markdown fences, no explanation.
Format:
[
  {{"step": 1, "tool": "tool_name_or_null", "args": {{}}, "description": "why", "depends_on": []}},
  ...
]
"""

_PLAN_USER = "Task: {task}"

# ─────────────────────────────────────────────────────────────────────────── #
#  Complexity heuristic (avoid planning overhead for simple requests)         #
# ─────────────────────────────────────────────────────────────────────────── #

_MULTI_STEP_KEYWORDS = {
    "then", "after", "first", "next", "and then", "followed by",
    "prepare", "generate", "summarize", "summarise", "analyze", "analyse",
    "note", "notes", "report", "schedule", "remind", "compile",
    "fetch and", "read and", "search and", "get and",
    "meeting", "presentation", "backup", "export",
}


def _looks_complex(task: str) -> bool:
    """Return True if the task probably requires >1 tool call."""
    lower = task.lower()
    hits = sum(1 for kw in _MULTI_STEP_KEYWORDS if kw in lower)
    # Also consider length — a long instruction is likely multi-step
    return hits >= 2 or len(task.split()) >= 15


# ─────────────────────────────────────────────────────────────────────────── #
#  Planner class                                                               #
# ─────────────────────────────────────────────────────────────────────────── #

class TaskPlanner:
    """
    Uses Gemini (one-shot, no tool calling) to plan multi-step tasks.

    Args:
        gemini_model: An initialised google.generativeai.GenerativeModel instance
                      WITHOUT tools (plain text generation mode).
        tool_names:   List of available tool names (from get_declarations()).
    """

    def __init__(self, gemini_model, tool_names: list[str]):
        self._model = gemini_model
        self._tool_names = tool_names

    # ---------------------------------------------------------------- #
    # Public API                                                        #
    # ---------------------------------------------------------------- #

    def should_plan(self, task: str) -> bool:
        """Return True if this task warrants a planning pass."""
        return self._model is not None and _looks_complex(task)

    def plan(self, task: str) -> TaskPlan:
        """
        Generate a TaskPlan for the given task.

        Returns an empty TaskPlan if planning fails or is not needed.
        Falls back gracefully — the agent loop will still run normally.
        """
        if self._model is None:
            return TaskPlan(task=task, steps=[])

        tools_str = ", ".join(self._tool_names)
        system_prompt = _PLAN_SYSTEM.format(tools=tools_str)
        user_prompt = _PLAN_USER.format(task=task)

        try:
            resp = self._model.generate_content(
                f"{system_prompt}\n\n{user_prompt}",
                generation_config={
                    "temperature": 0.1,
                    "max_output_tokens": 512,
                },
            )
            raw = resp.text if hasattr(resp, "text") else str(resp)
            steps = self._parse_steps(raw)
            plan = TaskPlan(task=task, steps=steps)
            if not plan.is_empty():
                log.info("Planner: %d-step plan for %r", len(steps), task[:60])
                log.debug("Planner: %s", plan.summary())
            return plan

        except Exception as exc:
            log.warning("Planner: failed to generate plan: %s", exc)
            return TaskPlan(task=task, steps=[])

    # ---------------------------------------------------------------- #
    # Internal                                                          #
    # ---------------------------------------------------------------- #

    def _parse_steps(self, raw: str) -> list[PlanStep]:
        """Extract the JSON step array from raw Gemini output."""
        # Strip markdown fences if present
        raw = re.sub(r"```[\w]*\n?", "", raw).strip()

        # Find the first [...] block
        match = re.search(r"\[[\s\S]*\]", raw)
        if not match:
            return []

        try:
            data = json.loads(match.group())
        except json.JSONDecodeError as exc:
            log.warning("Planner: JSON parse error: %s", exc)
            return []

        if not isinstance(data, list):
            return []

        steps: list[PlanStep] = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            try:
                step = PlanStep(
                    step=int(item.get("step", i + 1)),
                    tool=item.get("tool") or None,
                    args=item.get("args") or {},
                    description=str(item.get("description", "")),
                    depends_on=[int(x) for x in item.get("depends_on", [])],
                )
                # Validate tool name is known
                if step.tool and step.tool not in self._tool_names:
                    log.warning(
                        "Planner: unknown tool '%s' in step %d — skipping",
                        step.tool, step.step,
                    )
                    continue
                steps.append(step)
            except Exception as exc:
                log.warning("Planner: could not parse step %s: %s", item, exc)
                continue

        return steps


# ─────────────────────────────────────────────────────────────────────────── #
#  Factory helper (creates a planner-mode Gemini model — no tool declarations)#
# ─────────────────────────────────────────────────────────────────────────── #

def make_planner(config: dict, tool_names: list[str]) -> Optional[TaskPlanner]:
    """
    Attempt to initialise a TaskPlanner using a stripped-down Gemini model.
    Returns None if Gemini is not configured.
    """
    import os
    try:
        import google.generativeai as genai
    except ImportError:
        return None

    llm_cfg = config.get("llm", {})
    if not llm_cfg.get("enabled", False):
        return None

    api_key = os.environ.get("GEMINI_API_KEY") or llm_cfg.get("gemini_api_key", "")
    if not api_key:
        return None

    try:
        genai.configure(api_key=api_key)
        model_name = llm_cfg.get("gemini_model", "gemini-2.5-flash")
        # No tools — plain text generation for the planning prompt
        plain_model = genai.GenerativeModel(
            model_name=model_name,
            generation_config={"temperature": 0.1, "max_output_tokens": 512},
        )
        return TaskPlanner(plain_model, tool_names)
    except Exception as exc:
        log.warning("make_planner: failed to init planner model: %s", exc)
        return None
