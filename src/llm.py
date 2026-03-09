"""
src/llm.py — Ollama adapter for natural language → structured intent.

Uses a local Ollama instance (free, offline) to parse natural language
into TCC command intents. Falls back gracefully if Ollama is offline.

Usage flow:
  "open youtube on my phone"
    → POST /api/generate (Ollama)
    → {"target":"phone","action":"launch","args":{"app":"youtube"}}
    → Intent object
    → executed normally
"""

import json
import requests
from typing import Optional
from src.parser import Intent

SYSTEM_PROMPT = """You are a command parser for TCC (Terminal Command Center), codename JARVIS.
Convert natural language input into a JSON command intent.
ONLY respond with a valid JSON object — no explanation, no markdown.

Available targets: system, phone, laptop, server, all
Available actions: info, screenshot, launch, open, lock, unlock, volume, brightness, push, pull, ls, run, notify, battery, reboot, shutdown, status

Examples:
Input: "take a screenshot of my phone"
Output: {"target":"phone","action":"screenshot","args":{}}

Input: "what is the battery level on my phone"
Output: {"target":"phone","action":"battery","args":{}}

Input: "open youtube on my phone"
Output: {"target":"phone","action":"launch","args":{"app":"youtube"}}

Input: "what is the disk usage on my laptop"
Output: {"target":"laptop","action":"run","args":{"cmd":"df -h"}}

Input: "set phone volume to 8"
Output: {"target":"phone","action":"volume","args":{"level":"8"}}

Input: "lock my phone"
Output: {"target":"phone","action":"lock","args":{}}

Input: "show system info"
Output: {"target":"system","action":"info","args":{}}

Input: "send hello to all devices"
Output: {"target":"all","action":"notify","args":{"message":"hello"}}

If the input cannot be mapped to a command, respond with:
{"target":null,"action":null,"args":{}}
"""


class LLMAdapter:
    def __init__(self, config: dict, logger):
        self.config = config
        self.logger = logger
        llm_cfg = config.get("llm", {})
        self.enabled = llm_cfg.get("enabled", False)
        self.host = llm_cfg.get("host", "http://localhost:11434").rstrip("/")
        self.model = llm_cfg.get("model", "mistral")
        self.timeout = int(llm_cfg.get("timeout", 30))
        self.threshold = float(llm_cfg.get("confidence_threshold", 0.85))

    def is_available(self) -> bool:
        """Check if Ollama is running and enabled."""
        if not self.enabled:
            return False
        try:
            resp = requests.get(f"{self.host}/api/tags", timeout=2)
            return resp.status_code == 200
        except Exception:
            return False

    def extract_intent(self, text: str) -> Optional[Intent]:
        """
        Ask Ollama to convert natural language text into an Intent.
        Returns None if extraction fails or result is ambiguous.
        """
        try:
            payload = {
                "model": self.model,
                "prompt": text,
                "system": SYSTEM_PROMPT,
                "stream": False,
                "format": "json",
            }
            resp = requests.post(
                f"{self.host}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            raw_response = data.get("response", "")

            parsed = json.loads(raw_response)

            target = parsed.get("target")
            action = parsed.get("action")

            if not target or not action:
                self.logger.debug(f"LLM could not map: '{text}'")
                return None

            intent = Intent(
                raw=text,
                target=str(target).lower(),
                action=str(action).lower(),
                args=parsed.get("args", {}),
            )
            self.logger.debug(f"LLM mapped '{text}' → {target} {action}")
            return intent

        except json.JSONDecodeError as e:
            self.logger.warning(f"LLM returned invalid JSON: {e}")
            return None
        except Exception as e:
            self.logger.warning(f"LLM extraction failed: {e}")
            return None
