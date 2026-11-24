"""LLM backends: client factory, per-backend clients, and the shared vLLM server.

    llm_client          create_client() factory, model aliases, config.yaml access
    openrouter_client   remote models via the OpenRouter API
    local_model_client  local HuggingFace transformers inference
    vllm_client         HTTP client for the shared vLLM server
    vllm_server         start / stop / status of the shared `vllm serve` process
"""
