"""
Local HuggingFace Model Client for the Verilog Debug Agent.

Downloads and runs inference locally using models from HuggingFace Hub.
Default model: Qwen/Qwen3-Coder-30B-A3B-Instruct (30B MoE, ~3B active params).

This provides an API-free alternative to OpenRouter for development, testing,
and offline debugging of the agentic system.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOCAL_MODELS = {
    "qwen-coder":    "Qwen/Qwen3-Coder-30B-A3B-Instruct",
    "qwen-coder-7b": "Qwen/Qwen2.5-Coder-7B-Instruct",
}

DEFAULT_LOCAL_MODEL = LOCAL_MODELS["qwen-coder-7b"]

# Fallback only — create_client() in llm_client.py reads config.yaml and
# passes the user-configured cache_dir.  This is used only when
# LocalModelClient is instantiated directly without a cache_dir argument.
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "verilog_debug_models"


class LocalModelClient:
    """Client for running inference with a locally-downloaded HuggingFace model."""

    def __init__(
        self,
        model_name: str = DEFAULT_LOCAL_MODEL,
        cache_dir: str | Path | None = None,
        temperature: float = 0.2,
        max_new_tokens: int = 8192,
        device: str = "auto",
        torch_dtype: str = "auto",
        gpu_ids: str | None = None,
        trust_remote_code: bool = True,
        repetition_penalty: float = 1.1,
    ):
        self.model_name = model_name
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self.temperature = temperature
        self.max_new_tokens = max_new_tokens
        self.device = device
        self.torch_dtype = torch_dtype
        self.gpu_ids = gpu_ids
        self.trust_remote_code = trust_remote_code
        self.repetition_penalty = repetition_penalty
        self.conversation_history: list[dict] = []

        self._model = None
        self._tokenizer = None
        self._loaded = False

    def _resolve_dtype(self):
        import torch
        if self.torch_dtype == "auto":
            if torch.cuda.is_available():
                return torch.bfloat16
            return torch.float32
        dtype_map = {
            "float16": torch.float16,
            "bfloat16": torch.bfloat16,
            "float32": torch.float32,
        }
        return dtype_map.get(self.torch_dtype, torch.float32)

    _ALLOC_CONF_APPLIED = False

    @staticmethod
    def _apply_cuda_alloc_conf():
        """Set PyTorch CUDA allocator settings to reduce fragmentation.
        Must be called *before* any CUDA allocation."""
        if LocalModelClient._ALLOC_CONF_APPLIED:
            return
        key = "PYTORCH_CUDA_ALLOC_CONF"
        existing = os.environ.get(key, "")
        if "expandable_segments" not in existing:
            new_val = "expandable_segments:True"
            if existing:
                new_val = existing + "," + new_val
            os.environ[key] = new_val
            logger.info("Set %s=%s", key, new_val)
        LocalModelClient._ALLOC_CONF_APPLIED = True

    def _build_max_memory(self, buffer_gib: float = 8.0) -> dict:
        """Build a max_memory dict that reserves *buffer_gib* on each visible GPU
        and allows CPU RAM as overflow.  The buffer covers other processes,
        PyTorch reserved-but-fragmented memory, and temporary tensors needed
        during weight conversion (MoE gate_up_proj concatenation etc.)."""
        import torch

        max_memory = {}
        for i in range(torch.cuda.device_count()):
            free, _total = torch.cuda.mem_get_info(i)
            usable = max(free / (1 << 30) - buffer_gib, 0)
            max_memory[i] = f"{usable:.0f}GiB"
            logger.info("  GPU %d: %.1f GiB free, reserving %.1f GiB buffer → usable %.0f GiB",
                        i, free / (1 << 30), buffer_gib, usable)
        max_memory["cpu"] = "96GiB"
        return max_memory

    def load_model(self):
        """Download (if needed) and load the model and tokenizer into memory."""
        if self._loaded:
            logger.info("Model already loaded: %s", self.model_name)
            return

        # Reduce CUDA memory fragmentation — must happen before any CUDA call.
        self._apply_cuda_alloc_conf()

        # CUDA_VISIBLE_DEVICES must be set before the first `import torch`.
        # The primary place this happens is in create_client() (llm_client.py)
        # before check_requirements() triggers the first torch import.
        # This is a defensive fallback for direct LocalModelClient usage.
        if self.gpu_ids:
            os.environ["CUDA_VISIBLE_DEVICES"] = self.gpu_ids
            logger.info("Set CUDA_VISIBLE_DEVICES=%s", self.gpu_ids)

        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError:
            raise ImportError(
                "Local model inference requires 'transformers' and 'torch'. "
                "Install with: pip install transformers torch accelerate"
            )

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        dtype = self._resolve_dtype()

        visible_count = torch.cuda.device_count()
        logger.info(
            "Loading model '%s' (dtype=%s, device=%s, gpu_ids=%s, visible_gpus=%d)...",
            self.model_name, dtype, self.device, self.gpu_ids or "all", visible_count,
        )
        logger.info("Cache directory: %s", self.cache_dir)
        for i in range(visible_count):
            name = torch.cuda.get_device_name(i)
            free, total = torch.cuda.mem_get_info(i)
            logger.info("  GPU %d (%s): %.1f GiB free / %.1f GiB total",
                        i, name, free / (1 << 30), total / (1 << 30))
        logger.info("This may take several minutes on first run (downloading weights).")

        # Release any cached memory before loading to give max contiguous space.
        if visible_count > 0:
            torch.cuda.empty_cache()

        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_name,
            cache_dir=str(self.cache_dir),
            trust_remote_code=self.trust_remote_code,
        )

        max_memory = self._build_max_memory() if visible_count > 0 else None

        offload_dir = self.cache_dir / "offload"
        offload_dir.mkdir(parents=True, exist_ok=True)

        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            cache_dir=str(self.cache_dir),
            dtype=dtype,
            device_map="auto",
            max_memory=max_memory,
            offload_folder=str(offload_dir),
            low_cpu_mem_usage=True,
            trust_remote_code=self.trust_remote_code,
        )

        self._loaded = True
        self.context_window_size = getattr(
            self._model.config, "max_position_embeddings", 262144
        )
        logger.info("Context window size: %d", self.context_window_size)
        logger.info("Model loaded successfully.")

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
        """Send a message and get a response using local inference."""
        if not self._loaded:
            self.load_model()

        import torch

        self.add_message("user", user_message)

        text = self._tokenizer.apply_chat_template(
            self.conversation_history,
            tokenize=False,
            add_generation_prompt=True,
        )

        inputs = self._tokenizer(text, return_tensors="pt").to(self._model.device)
        input_len = inputs["input_ids"].shape[-1]

        logger.info(
            "Generating — input_tokens=%d, max_new_tokens=%d, temp=%.2f",
            input_len, self.max_new_tokens, self.temperature,
        )

        generate_kwargs = {
            "max_new_tokens": self.max_new_tokens,
            "do_sample": self.temperature > 0,
            "top_p": 0.9,
            "use_cache": True,
            "repetition_penalty": self.repetition_penalty,
        }
        if self.temperature > 0:
            generate_kwargs["temperature"] = self.temperature

        t0 = time.perf_counter()
        with torch.no_grad():
            output_ids = self._model.generate(**inputs, **generate_kwargs)
        elapsed = time.perf_counter() - t0

        new_tokens = output_ids[0][input_len:]
        n_generated = len(new_tokens)
        tok_per_sec = n_generated / elapsed if elapsed > 0 else 0.0
        logger.info(
            "Generation done — %d tokens in %.1fs (%.1f tok/s)",
            n_generated, elapsed, tok_per_sec,
        )

        assistant_message = self._tokenizer.decode(new_tokens, skip_special_tokens=True)

        import re
        think_match = re.search(r"<think>.*?</think>", assistant_message, flags=re.DOTALL)
        if think_match:
            think_len = len(think_match.group())
            logger.info("Stripped <think> block (%d chars) from response", think_len)
        assistant_message = re.sub(
            r"<think>.*?</think>\s*", "", assistant_message, flags=re.DOTALL,
        ).strip()

        self.add_message("assistant", assistant_message)

        logger.info(
            "Response length: %d chars",
            len(assistant_message),
        )

        metrics = {
            "input_tokens": input_len,
            "output_tokens": n_generated,
            "total_tokens": input_len + n_generated,
            "latency_s": round(elapsed, 3),
            "tokens_per_second": round(tok_per_sec, 1),
        }

        return assistant_message, metrics

    def chat_json(self, user_message: str) -> dict:
        """Send a message and parse the response as JSON."""
        raw = self.chat(user_message)
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
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

    def unload(self):
        """Free model from memory."""
        if self._model is not None:
            del self._model
            self._model = None
        if self._tokenizer is not None:
            del self._tokenizer
            self._tokenizer = None
        self._loaded = False

        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

        logger.info("Model unloaded.")

    @staticmethod
    def check_requirements() -> dict:
        """Check whether local inference dependencies are available.

        IMPORTANT: This is called after CUDA_VISIBLE_DEVICES is set by
        create_client() so that torch only sees the configured GPUs.
        """
        status = {"torch": False, "transformers": False, "cuda": False, "gpu_name": None}
        try:
            import torch
            status["torch"] = True
            status["cuda"] = torch.cuda.is_available()
            if status["cuda"] and torch.cuda.device_count() > 0:
                status["gpu_name"] = torch.cuda.get_device_name(0)
                status["gpu_memory_gb"] = round(
                    torch.cuda.get_device_properties(0).total_memory / 1e9, 1
                )
                status["gpu_count"] = torch.cuda.device_count()
        except ImportError:
            pass
        try:
            import transformers
            status["transformers"] = True
            status["transformers_version"] = transformers.__version__
        except ImportError:
            pass
        return status
