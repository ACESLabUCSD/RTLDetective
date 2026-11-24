"""
OpenRouter LLM client for the Verilog Debug Agent.

Supports model selection and structured conversation with any LLM
available through the OpenRouter API.
"""

import os
import json
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "anthropic/claude-sonnet-4"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

RECOMMENDED_MODELS = {
    "claude-sonnet": "anthropic/claude-sonnet-4",
    "claude-haiku": "anthropic/claude-3.5-haiku",
    "gpt-4o": "openai/gpt-4o",
    "gpt-4o-mini": "openai/gpt-4o-mini",
    "deepseek-chat": "deepseek/deepseek-chat",
    "llama-70b": "meta-llama/llama-3.1-70b-instruct",
    "gemini-pro": "google/gemini-2.0-flash-001",
}


class OpenRouterClient:
    """Client for interacting with LLMs via OpenRouter."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.2,
        max_tokens: int = 8192,
        site_url: str = "https://verilog-debug-agent",
        app_name: str = "VerilogDebugAgent",
    ):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "OpenRouter API key required. Set OPENROUTER_API_KEY env var "
                "or pass api_key parameter."
            )
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.site_url = site_url
        self.app_name = app_name
        self.conversation_history: list[dict] = []

    def _build_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.site_url,
            "X-Title": self.app_name,
        }

    def set_model(self, model: str):
        """Switch to a different model. Accepts short names or full model IDs."""
        if model in RECOMMENDED_MODELS:
            self.model = RECOMMENDED_MODELS[model]
        else:
            self.model = model
        logger.info("Model set to: %s", self.model)

    def set_system_prompt(self, system_prompt: str):
        """Set or replace the system prompt at the start of conversation."""
        if self.conversation_history and self.conversation_history[0]["role"] == "system":
            self.conversation_history[0]["content"] = system_prompt
        else:
            self.conversation_history.insert(0, {
                "role": "system",
                "content": system_prompt,
            })

    def add_message(self, role: str, content: str):
        """Append a message to the conversation history."""
        self.conversation_history.append({"role": role, "content": content})

    def chat(self, user_message: str) -> str:
        """Send a message and get a response, maintaining conversation state."""
        self.add_message("user", user_message)

        payload = {
            "model": self.model,
            "messages": self.conversation_history,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        try:
            response = requests.post(
                OPENROUTER_API_URL,
                headers=self._build_headers(),
                json=payload,
                timeout=120,
            )
            response.raise_for_status()
            data = response.json()

            if "choices" not in data or not data["choices"]:
                error_msg = data.get("error", {}).get("message", "Unknown error")
                raise RuntimeError(f"OpenRouter API error: {error_msg}")

            assistant_message = data["choices"][0]["message"]["content"]
            self.add_message("assistant", assistant_message)

            usage = data.get("usage", {})
            logger.debug(
                "Tokens — prompt: %d, completion: %d, total: %d",
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                usage.get("total_tokens", 0),
            )

            return assistant_message

        except requests.exceptions.RequestException as e:
            logger.error("OpenRouter request failed: %s", e)
            raise

    def chat_json(self, user_message: str) -> dict:
        """Send a message and parse the response as JSON."""
        raw = self.chat(user_message)
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]  # remove opening fence
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return json.loads(text)

    def reset_conversation(self):
        """Clear conversation history (preserves system prompt if set)."""
        system = None
        if self.conversation_history and self.conversation_history[0]["role"] == "system":
            system = self.conversation_history[0]
        self.conversation_history = []
        if system:
            self.conversation_history.append(system)

    @staticmethod
    def list_models() -> dict:
        """Return the recommended model shortnames and their full IDs."""
        return dict(RECOMMENDED_MODELS)
