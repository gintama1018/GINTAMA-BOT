"""
src/parser.py — Command grammar tokenizer.

Grammar:
    command  = target  action  [arguments]
    target   = device name | special keyword
    action   = verb from permitted list
    arguments = key=value pairs | positional strings | --flags

Special commands (no target):
    devices, logs, skills, help, exit, quit, clear

NLP fallback:
    If first token is not a known target, return Intent with
    target="__nlp__" so the LLM layer can handle it.
"""

import shlex
from dataclasses import dataclass, field
from typing import Any, List, Optional

TARGETS = {"system", "phone", "laptop", "server", "all"}

SPECIAL_COMMANDS = {"devices", "logs", "skills", "help", "exit", "quit", "clear", "sessions", "memory"}

ACTIONS = {
    "info", "screenshot", "launch", "open", "lock", "unlock",
    "volume", "brightness", "push", "pull", "ls", "run",
    "notify", "battery", "reboot", "shutdown", "status",
}


@dataclass
class Intent:
    raw: str
    target: str = ""
    action: str = ""
    args: dict = field(default_factory=dict)
    flags: dict = field(default_factory=dict)
    special: bool = False
    error: str = ""


def parse(raw_input: str) -> Intent:
    """Parse a raw command string into an Intent object."""
    raw = raw_input.strip()
    if not raw:
        return Intent(raw=raw, error="empty input")

    try:
        tokens = shlex.split(raw)
    except ValueError as e:
        return Intent(raw=raw, error=f"parse error: {e}")

    if not tokens:
        return Intent(raw=raw, error="empty input")

    first = tokens[0].lower()

    # Special commands need no target
    if first in SPECIAL_COMMANDS:
        intent = Intent(raw=raw, action=first, special=True)
        _parse_flags(intent, tokens[1:])
        return intent

    # Unknown first token — try pattern matching before NLP
    if first not in TARGETS:
        # If the first word IS a known action verb, assume 'system' target —
        # BUT only for short/simple commands (e.g. "open chrome", "battery", "screenshot").
        # Longer inputs that look like natural language (contain "and", pronouns,
        # multiple action verbs) should go to the LLM so nothing is silently dropped.
        if first in ACTIONS:
            _NLP_WORDS = {"and", "my", "me", "the", "a", "please", "can", "could", "then", "also", "with"}
            is_compound = len(tokens) > 3 and (
                any(t.lower() in _NLP_WORDS for t in tokens[1:])
                or any(t.lower() in ACTIONS for t in tokens[1:])  # multiple verbs
            )
            if not is_compound:
                intent = Intent(raw=raw, target="system", action=first)
                _parse_args(intent, tokens[1:])
                return intent
        # Otherwise (or compound NL command) — hand off to LLM / NLP layer
        return Intent(
            raw=raw,
            target="__nlp__",
            action="__nlp__",
            args={"text": raw},
        )

    intent = Intent(raw=raw, target=first)

    if len(tokens) < 2:
        return Intent(raw=raw, error=f"missing action for target '{first}'")

    intent.action = tokens[1].lower()
    _parse_args(intent, tokens[2:])
    return intent


def _parse_args(intent: Intent, tokens: List[str]) -> None:
    """Parse remaining tokens into positional args and --flags."""
    positional: List[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--"):
            key = tok[2:]
            if key and i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                intent.flags[key] = tokens[i + 1]
                i += 2
            else:
                intent.flags[key] = True
                i += 1
        elif "=" in tok and not tok.startswith("="):
            k, v = tok.split("=", 1)
            intent.args[k] = v
            i += 1
        else:
            positional.append(tok)
            i += 1

    _map_positionals(intent, positional)


def _parse_flags(intent: Intent, tokens: List[str]) -> None:
    """Parse flags for special commands."""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--"):
            key = tok[2:]
            if key and i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                intent.flags[key] = tokens[i + 1]
                i += 2
            else:
                intent.flags[key] = True
                i += 1
        else:
            # Positional arg for special commands (e.g. 'exit')
            intent.args.setdefault("_", []).append(tok)
            i += 1


def _map_positionals(intent: Intent, positional: List[str]) -> None:
    """Map positional tokens to named args based on action."""
    action = intent.action

    if action in ("launch", "open"):
        if positional:
            intent.args["app"] = positional[0]

    elif action == "volume":
        if positional:
            intent.args["level"] = positional[0]

    elif action == "brightness":
        if positional:
            intent.args["level"] = positional[0]

    elif action == "push":
        if len(positional) >= 2:
            intent.args["src"] = positional[0]
            intent.args["dst"] = positional[1]
        elif positional:
            intent.args["src"] = positional[0]

    elif action == "pull":
        if len(positional) >= 2:
            intent.args["src"] = positional[0]
            intent.args["dst"] = positional[1]
        elif positional:
            intent.args["src"] = positional[0]

    elif action == "ls":
        if positional:
            intent.args["path"] = positional[0]

    elif action == "run":
        if positional:
            intent.args["cmd"] = " ".join(positional)

    elif action == "notify":
        if positional:
            intent.args["message"] = " ".join(positional)

    elif action in ("info", "screenshot", "battery", "lock", "unlock",
                    "reboot", "shutdown", "status"):
        pass  # no positional args needed

    else:
        if positional:
            intent.args["_args"] = positional
