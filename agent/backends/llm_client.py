"""
Unified LLM Client Factory.

Creates the appropriate client (OpenRouter API, local HuggingFace model, or
local vLLM engine). Reads ``config.yaml`` for local-model settings
(cache_dir, torch_dtype, device, vllm) so user configuration is respected.

The backend is chosen explicitly with ``backend=`` ("openrouter", "hf",
"vllm"; the agent's ``--backend`` flag) and defaults to "hf".
"""

import logging
from pathlib import Path

from agent.backends.config import CONFIG_PATH, PROJECT_ROOT, load_config_section
from agent.backends.openrouter_client import OpenRouterClient, RECOMMENDED_MODELS
from agent.backends.vllm_client import VLLMClient

logger = logging.getLogger(__name__)

LOCAL_PREFIX = "local/"

LOCAL_MODELS = {
    "qwen-coder-30b":    "Qwen/Qwen3-Coder-30B-A3B-Instruct",
    "qwen-coder-7b": "Qwen/Qwen2.5-Coder-7B-Instruct",
    "qwen-coder-1.5b":  "Qwen/Qwen2.5-Coder-1.5B-Instruct",
    "qwen-coder-next": "Qwen/Qwen3-Coder-Next",
}

BACKENDS = ("openrouter", "hf", "vllm")
DEFAULT_BACKEND = "hf"

ALL_MODELS = {**RECOMMENDED_MODELS, **{k: LOCAL_PREFIX + v for k, v in LOCAL_MODELS.items()}}




def _load_local_model_config() -> dict:
    """Read the local_model section from config.yaml (if it exists)."""
    return load_config_section("local_model")


def resolve_model_name(model: str) -> str:
    """Resolve short aliases to full model identifiers."""
    if model in RECOMMENDED_MODELS:
        return RECOMMENDED_MODELS[model]
    if model in LOCAL_MODELS:
        return LOCAL_PREFIX + LOCAL_MODELS[model]
    return model


def _bare_model_id(model: str) -> str:
    """Resolve aliases and strip the local/ prefix, leaving the plain model ID."""
    return resolve_model_name(model).removeprefix(LOCAL_PREFIX)


def create_client(
    model: str,
    temperature: float = 0.2,
    max_tokens: int = 8192,
    backend: str = DEFAULT_BACKEND,
):
    """
    Create and return the appropriate LLM client.

    For local models the ``local_model`` section of ``config.yaml`` is read.

    Returns an OpenRouterClient, LocalModelClient or VLLMClient, all of which
    expose the same interface: set_system_prompt(), chat(), chat_json(),
    reset_conversation().

    ``backend`` ("openrouter", "hf", "vllm") picks the client.
    """
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend {backend!r}; choose from {', '.join(BACKENDS)}")
    model_id = _bare_model_id(model)

    if backend in ("hf", "vllm") and model in RECOMMENDED_MODELS:
        raise ValueError(
            f"'{model}' is an OpenRouter model; run it with --backend openrouter."
        )

    if backend == "vllm":
        from agent.backends.vllm_server import load_settings

        hf_name = model_id
        logger.info("Creating vLLM (HTTP server) client: %s", hf_name)
        vcfg = load_config_section("vllm")
        client = VLLMClient(
            model_name=hf_name,
            temperature=vcfg.get("temperature", temperature),
            top_p=vcfg.get("top_p", 0.9),
            repetition_penalty=vcfg.get("repetition_penalty", 1.1),
            max_new_tokens=vcfg.get("max_new_tokens", max_tokens),
            seed=vcfg.get("seed", 0),
            settings=load_settings(),
        )
        client.load_model()
        return client
    
    elif backend == "hf":
        from agent.backends.local_model_client import LocalModelClient

        hf_name = model_id
        logger.info("Creating local model client: %s", hf_name)

        lcfg = _load_local_model_config()
        cache_dir = lcfg.get("cache_dir")
        torch_dtype = lcfg.get("torch_dtype", "auto")
        device = lcfg.get("device", "auto")
        gpu_ids = lcfg.get("gpu_ids")

        if cache_dir:
            cache_dir = str(Path(cache_dir).expanduser())
            logger.info("Using cache_dir from config.yaml: %s", cache_dir)
        if gpu_ids:
            import os
            os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_ids)
            logger.info("Set CUDA_VISIBLE_DEVICES=%s (before torch init)", gpu_ids)

        reqs = LocalModelClient.check_requirements()
        if not reqs["torch"]:
            raise ImportError(
                "Local inference requires PyTorch. "
                "Install with: pip install torch"
            )
        if not reqs["transformers"]:
            raise ImportError(
                "Local inference requires transformers. "
                "Install with: pip install transformers accelerate"
            )
        logger.info(
            "System check — CUDA: %s, GPU: %s",
            reqs["cuda"], reqs.get("gpu_name", "N/A"),
        )

        client = LocalModelClient(
            model_name=hf_name,
            cache_dir=cache_dir,
            temperature=temperature,
            max_new_tokens=max_tokens,
            device=device,
            torch_dtype=torch_dtype,
            gpu_ids=str(gpu_ids) if gpu_ids is not None else None,
        )
        client.load_model()
        return client
    else:
        logger.info("Creating OpenRouter client: %s", model_id)
        return OpenRouterClient(
            model=model_id,
            temperature=temperature,
            max_tokens=max_tokens,
        )


def list_all_models() -> dict:
    """Return all available models (both remote and local)."""
    return dict(ALL_MODELS)
