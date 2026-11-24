"""
vLLM client for the Verilog Debug Agent (HTTP).

Talks to a shared `vllm serve` process (see agent/backends/vllm_server.py) through its API.
"""

from __future__ import annotations

import atexit
import itertools
import json
import logging
import os
import re
import time
import requests
from agent.backends.vllm_server import start_server, stop_server

logger = logging.getLogger(__name__)


class ContextOverflowError(RuntimeError):
    """The conversation no longer fits in the model's context window."""


class _ServerTokenizer:
    """Minimal tokenizer backed by the server's /tokenize endpoint."""

    def __init__(self, client: "VLLMClient"):
        self._client = client

    def encode(self, text: str) -> list[int]:
        r = self._client._post("/tokenize", {
            "model": self._client.model_name, "prompt": text, "add_special_tokens": False,
        }, timeout=60)
        return r["tokens"]


class VLLMClient:
    """Client for a shared vLLM OpenAI-compatible server."""

    def __init__(
        self,
        model_name: str,
        temperature: float = 0.2,
        max_new_tokens: int = 8192,
        top_p: float = 0.9,
        repetition_penalty: float = 1.1,
        seed: int | None = 0,
        request_timeout_s: float = 3600,
        settings: dict | None = None,
    ):
        self.model_name = model_name
        self.temperature = temperature
        self.max_new_tokens = max_new_tokens
        self.top_p = top_p
        self.repetition_penalty = repetition_penalty
        self.seed = seed
        self._request_no = itertools.count()   # chat requests sent by this client (see chat())
        self.request_timeout_s = request_timeout_s
        self.settings = settings
        self.base_url: str | None = None
        self.context_window_size: int | None = None
        self.conversation_history: list[dict] = []
        self._system_prompt = ""
        self.prompt_log_path: str | os.PathLike | None = None
        self.log_context: dict = {}
        self._tokenizer = _ServerTokenizer(self)
        self._loaded = False

    # --- server connection ---------------------------------------------------

    def load_model(self):
        """Connect to the shared server, starting it if nothing is listening.
        A server this process starts (a single run) is stopped when the process exits.
        """
    
        self.base_url, started = start_server(self.model_name, self.settings)
        if started:
            if os.environ.get("VLLM_KEEP_SERVER") == "1":
                logger.info("Started the vLLM server; leaving it running (VLLM_KEEP_SERVER=1).")
            else:
                logger.info("Started the vLLM server for this run; it stops when this run ends "
                            "(set VLLM_KEEP_SERVER=1 to keep it).")
                atexit.register(stop_server)
        r = requests.get(f"{self.base_url}/v1/models", timeout=30)
        r.raise_for_status()
        info = next(m for m in r.json()["data"] if m["id"] == self.model_name)
        self.context_window_size = info.get("max_model_len")
        self._loaded = True
        logger.info("Using vLLM server %s (%s), context window %s",
                    self.base_url, self.model_name, self.context_window_size)

    def _post(self, path: str, body: dict, timeout: float | None = None) -> dict:
        if not self._loaded:
            self.load_model()
        try:
            r = requests.post(f"{self.base_url}{path}", json=body,
                              timeout=timeout or self.request_timeout_s)
        except requests.ConnectionError:
            logger.warning("Lost connection to the vLLM server; reconnecting...")
            self.load_model()
            r = requests.post(f"{self.base_url}{path}", json=body,
                              timeout=timeout or self.request_timeout_s)
        if r.status_code >= 400:
            raise RuntimeError(f"vLLM server error {r.status_code} on {path}: {r.text[:500]}")
        return r.json()

    def _post_stream(self, path: str, body: dict) -> tuple[dict, float | None]:
        """POST a streaming chat request and rebuild the full response from the chunks.

        Returns (response, ttft_s): `response` has the same shape as a non-streaming reply
        (choices[0].message.content, finish_reason, usage); `ttft_s` is the time from sending
        the request to the first generated token (None if nothing was generated).
        """
        if not self._loaded:
            self.load_model()
        body = dict(body, stream=True, stream_options={"include_usage": True})
        t0 = time.perf_counter()
        try:
            r = requests.post(f"{self.base_url}{path}", json=body, stream=True,
                              timeout=self.request_timeout_s)
        except requests.ConnectionError:
            logger.warning("Lost connection to the vLLM server; reconnecting...")
            self.load_model()
            t0 = time.perf_counter()
            r = requests.post(f"{self.base_url}{path}", json=body, stream=True,
                              timeout=self.request_timeout_s)
        with r:
            if r.status_code >= 400:
                raise RuntimeError(f"vLLM server error {r.status_code} on {path}: {r.text[:500]}")
            ttft = None
            parts: list[str] = []
            finish_reason = None
            usage: dict = {}
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for ch in chunk.get("choices") or []:
                    delta = ch.get("delta") or {}
                    text = delta.get("content") or ""
                    if ttft is None and (text or delta.get("reasoning_content")):
                        ttft = time.perf_counter() - t0
                    parts.append(text)
                    if ch.get("finish_reason"):
                        finish_reason = ch["finish_reason"]
        resp = {"choices": [{"message": {"role": "assistant", "content": "".join(parts)},
                             "finish_reason": finish_reason}],
                "usage": usage}
        return resp, ttft

    def _save_call(self, body: dict, raw_reply: str, metrics: dict):
        """Append one request/response pair to prompt_log_path (no-op when it is unset)."""
        if not self.prompt_log_path:
            return
        record = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            **self.log_context,
            "request": {k: v for k, v in body.items() if k not in ("stream", "stream_options")},
            "response": raw_reply,
            "metrics": metrics,
        }
        try:
            with open(self.prompt_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("Could not save the prompt to %s: %s", self.prompt_log_path, e)

    def _count_prompt_tokens(self, messages: list[dict]) -> int:
        return self._post("/tokenize", {"model": self.model_name, "messages": messages,
                                        "add_generation_prompt": True}, timeout=60)["count"]

    # --- conversation ----------------------------------------------------------

    def set_system_prompt(self, system_prompt: str):
        """Set or replace the system prompt at the start of conversation."""
        self._system_prompt = system_prompt
        if self.conversation_history and self.conversation_history[0]["role"] == "system":
            self.conversation_history[0]["content"] = system_prompt
        else:
            self.conversation_history.insert(0, {"role": "system", "content": system_prompt})

    def add_message(self, role: str, content: str):
        self.conversation_history.append({"role": role, "content": content})

    def reset_conversation(self):
        """Clear conversation history (keeps the system prompt)."""
        system = None
        if self.conversation_history and self.conversation_history[0]["role"] == "system":
            system = self.conversation_history[0]
        self.conversation_history = [system] if system else []

    def chat(self, user_message: str):
        """Send a message; returns (assistant_message, metrics)."""
        if not self._loaded:
            self.load_model()
        self.add_message("user", user_message)

        prompt_tokens = self._count_prompt_tokens(self.conversation_history)
        room = (self.context_window_size or 0) - prompt_tokens
        # Long runs (e.g. 10 iterations) can outgrow the context window. Each refinement prompt
        # carries the current code and test results, so the oldest middle turns can go: keep the
        # system prompt, the first exchange (design description + buggy code) and the new message.
        reserve = min(self.max_new_tokens, 8192)
        trimmed = 0
        while self.context_window_size and room < reserve and len(self.conversation_history) > 4:
            del self.conversation_history[3:5]   # the oldest (user, assistant) pair after the first exchange
            trimmed += 2
            prompt_tokens = self._count_prompt_tokens(self.conversation_history)
            room = self.context_window_size - prompt_tokens
        if trimmed:
            logger.warning("Context nearly full: dropped the %d oldest messages; prompt is now %d tokens",
                           trimmed, prompt_tokens)
        if self.context_window_size and room < 1:
            self.conversation_history.pop()
            raise ContextOverflowError(
                f"prompt is {prompt_tokens} tokens; context window is {self.context_window_size}")
        max_tokens = min(self.max_new_tokens, room) if self.context_window_size else self.max_new_tokens

        # Sampling settings come from config.yaml (vllm: temperature, top_p, ...).
        body = {
            "model": self.model_name,
            "messages": list(self.conversation_history),   # snapshot: the reply is appended later
            "max_tokens": max_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p if self.temperature > 0 else 1.0,
            "repetition_penalty": self.repetition_penalty,
        }
        if self.seed is not None:
            # seed + request number: the same rule as the MEIC and UVLLM baselines, so a run is
            # reproducible from its base seed and no two requests share a sampling seed.
            body["seed"] = self.seed + next(self._request_no)

        t0 = time.perf_counter()
        resp, ttft = self._post_stream("/v1/chat/completions", body)
        elapsed = time.perf_counter() - t0

        choice = resp["choices"][0]
        usage = resp.get("usage") or {}
        input_len = usage.get("prompt_tokens", prompt_tokens)
        n_generated = usage.get("completion_tokens", 0)
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        tok_per_sec = n_generated / elapsed if elapsed > 0 else 0.0
        # Decode phase = everything after the first token; its rate excludes prompt processing.
        decode_s = elapsed - ttft if ttft is not None else None
        decode_tps = (n_generated - 1) / decode_s if decode_s and n_generated > 1 else None
        logger.info(
            "Generation done — input_tokens=%d (cached %d), TTFT %s, %d tokens in %.1fs (%.1f tok/s), finish=%s",
            input_len, cached, f"{ttft * 1000:.0f}ms" if ttft is not None else "n/a",
            n_generated, elapsed, tok_per_sec, choice.get("finish_reason"),
        )

        raw_reply = choice["message"].get("content") or ""
        assistant_message = raw_reply
        think_match = re.search(r"<think>.*?</think>", assistant_message, flags=re.DOTALL)
        if think_match:
            logger.info("Stripped <think> block (%d chars) from response", len(think_match.group()))
        assistant_message = re.sub(r"<think>.*?</think>\s*", "", assistant_message,
                                   flags=re.DOTALL).strip()

        self.add_message("assistant", assistant_message)
        logger.info("Response length: %d chars", len(assistant_message))

        metrics = {
            "input_tokens": input_len,
            "cached_input_tokens": cached,
            "output_tokens": n_generated,
            "total_tokens": input_len + n_generated,
            "latency_s": round(elapsed, 3),
            "ttft_s": round(ttft, 4) if ttft is not None else None,
            "decode_s": round(decode_s, 3) if decode_s is not None else None,
            "decode_tokens_per_second": round(decode_tps, 1) if decode_tps else None,
            "tokens_per_second": round(tok_per_sec, 1),
            "finish_reason": choice.get("finish_reason"),
            "trimmed_messages": trimmed,
        }
        self._save_call(body, raw_reply, metrics)
        return assistant_message, metrics

    def chat_json(self, user_message: str) -> dict:
        """Send a message and parse the response as JSON."""
        raw, _ = self.chat(user_message)
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return json.loads(text)

    def unload(self):
        """Nothing to free locally; the shared server keeps running."""
        self._loaded = False

    @staticmethod
    def check_requirements() -> dict:
        """vLLM is only needed by the server process (agent/backends/vllm_server.py)."""
        status = {"vllm": False}
        try:
            import vllm
            status["vllm"] = True
            status["vllm_version"] = vllm.__version__
        except ImportError:
            pass
        return status
