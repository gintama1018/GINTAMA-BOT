"""
src/llm.py — Multi-backend LLM adapter for natural language → structured intent.

Supports two backends selectable via config.toml [llm] backend setting:

  backend = "gemini"   — Google Gemini API (cloud, free tier). Default for now.
                         Requires: pip install google-generativeai
                         Set GEMINI_API_KEY env var  OR  gemini_api_key in config.

  backend = "ollama"   — Local Ollama (fully offline, free, private).
                         Switch to this once Ollama is installed.
                         Set: ollama pull mistral  then backend = "ollama" in config.

Usage flow (same regardless of backend):
  "open youtube on my phone"
    → LLMAdapter.extract_intent()
    → {"target":"phone","action":"launch","args":{"app":"youtube"}}
    → Intent object → executed normally
"""

import json
import os
import re
import requests
from typing import Optional
from src.parser import Intent

# -------------------------------------------------------------------- #
# Shared system prompt — identical for both backends                   #
# -------------------------------------------------------------------- #

SYSTEM_PROMPT = """You are a command parser for TCC (Terminal Command Center), codename JARVIS.
Convert natural language input into a structured JSON command intent.
ONLY respond with a valid JSON object — no explanation, no markdown, no code block.

Available targets: system, phone, laptop, server, all
Available actions: info, screenshot, launch, open, lock, unlock, volume, brightness,
                   push, pull, ls, run, notify, battery, reboot, shutdown, status

Examples:
Input: "open camera and take my selfie"
Output: {"target":"system","action":"launch","args":{"app":"camera"}}

Input: "take a selfie"
Output: {"target":"system","action":"launch","args":{"app":"camera"}}

Input: "take a photo"
Output: {"target":"system","action":"launch","args":{"app":"camera"}}

Input: "capture my screen"
Output: {"target":"system","action":"screenshot","args":{}}

Input: "take a screenshot of my phone"
Output: {"target":"phone","action":"screenshot","args":{}}

Input: "what is the battery level on my phone"
Output: {"target":"phone","action":"battery","args":{}}

Input: "open youtube on my phone"
Output: {"target":"phone","action":"launch","args":{"app":"youtube"}}

Input: "what is the disk usage on my laptop"
Output: {"target":"laptop","action":"info","args":{}}

Input: "set phone volume to 8"
Output: {"target":"phone","action":"volume","args":{"level":"8"}}

Input: "lock my phone"
Output: {"target":"phone","action":"lock","args":{}}

Input: "show system info"
Output: {"target":"system","action":"info","args":{}}

Input: "send hello world notification to all devices"
Output: {"target":"all","action":"notify","args":{"message":"hello world"}}

Input: "open calculator on this computer"
Output: {"target":"system","action":"launch","args":{"app":"calculator"}}

Input: "brightness 200 on phone"
Output: {"target":"phone","action":"brightness","args":{"level":"200"}}

If the input is a compound command with multiple actions, respond with the PRIMARY (first) action only.
If the input cannot be mapped to any command, respond with:
{"target":null,"action":null,"args":{}}
"""


def _parse_llm_json(raw: str) -> Optional[dict]:
    """Extract JSON from LLM response, handling markdown code blocks."""
    raw = raw.strip()
    # Strip ```json ... ``` or ``` ... ``` wrappers
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract the first {...} block
        m = re.search(r"\{.*?\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        return None


# -------------------------------------------------------------------- #
# LLMAdapter — public interface                                        #
# -------------------------------------------------------------------- #

class LLMAdapter:
    def __init__(self, config: dict, logger):
        self.config = config
        self.logger = logger
        llm_cfg = config.get("llm", {})
        self.enabled  = llm_cfg.get("enabled", False)
        self.backend  = llm_cfg.get("backend", "gemini").lower()
        self.timeout  = int(llm_cfg.get("timeout", 30))

        # Gemini settings
        # Priority: env var → config → None
        self._gemini_api_key = (
            os.environ.get("GEMINI_API_KEY")
            or llm_cfg.get("gemini_api_key", "")
        )
        self._gemini_model = llm_cfg.get("gemini_model", "gemini-2.5-flash")

        # Ollama settings
        self._ollama_host  = llm_cfg.get("host", "http://localhost:11434").rstrip("/")
        self._ollama_model = llm_cfg.get("model", "mistral")

        # Lazy-init Gemini client
        self._gemini_client = None

    # ---------------------------------------------------------------- #
    # Availability check                                               #
    # ---------------------------------------------------------------- #

    def is_available(self) -> bool:
        """Return True if the configured backend is reachable and enabled."""
        if not self.enabled:
            return False
        if self.backend == "gemini":
            return bool(self._gemini_api_key)
        if self.backend == "ollama":
            try:
                resp = requests.get(f"{self._ollama_host}/api/tags", timeout=2)
                return resp.status_code == 200
            except Exception:
                return False
        return False

    # ---------------------------------------------------------------- #
    # Main interface                                                    #
    # ---------------------------------------------------------------- #

    def extract_intent(self, text: str) -> Optional[Intent]:
        """
        Convert natural language text into a structured Intent.
        Routes to Gemini or Ollama based on config.
        Returns None on failure.
        """
        if self.backend == "gemini":
            return self._gemini_extract(text)
        if self.backend == "ollama":
            return self._ollama_extract(text)
        self.logger.warning(f"Unknown LLM backend: {self.backend}")
        return None

    # ---------------------------------------------------------------- #
    # Gemini backend                                                   #
    # ---------------------------------------------------------------- #

    def _get_gemini_client(self):
        if self._gemini_client is None:
            try:
                import google.generativeai as genai
                genai.configure(api_key=self._gemini_api_key)
                self._gemini_client = genai.GenerativeModel(
                    model_name=self._gemini_model,
                    system_instruction=SYSTEM_PROMPT,
                )
            except ImportError:
                self.logger.warning("google-generativeai not installed. Run: pip install google-generativeai")
                return None
        return self._gemini_client

    def _gemini_extract(self, text: str) -> Optional[Intent]:
        client = self._get_gemini_client()
        if not client:
            return None
        try:
            response = client.generate_content(
                text,
                generation_config={
                    "temperature": 0,
                    "max_output_tokens": 400,
                },
            )
            raw = response.text.strip()
            parsed = _parse_llm_json(raw)
            return self._build_intent(text, parsed)
        except Exception as e:
            self.logger.warning(f"Gemini extraction failed: {e}")
            return None

    # ---------------------------------------------------------------- #
    # Ollama backend                                                   #
    # ---------------------------------------------------------------- #

    def _ollama_extract(self, text: str) -> Optional[Intent]:
        try:
            payload = {
                "model": self._ollama_model,
                "prompt": text,
                "system": SYSTEM_PROMPT,
                "stream": False,
                "format": "json",
            }
            resp = requests.post(
                f"{self._ollama_host}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "")
            parsed = _parse_llm_json(raw)
            return self._build_intent(text, parsed)
        except Exception as e:
            self.logger.warning(f"Ollama extraction failed: {e}")
            return None

    # ---------------------------------------------------------------- #
    # Shared intent builder                                            #
    # ---------------------------------------------------------------- #

    def _build_intent(self, text: str, parsed: Optional[dict]) -> Optional[Intent]:
        if not parsed:
            self.logger.debug(f"LLM returned unparseable response for: '{text}'")
            return None
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
        self.logger.debug(f"LLM ({self.backend}) mapped '{text}' → {target} {action}")
        return intent

