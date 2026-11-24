"""vLLM stand-in for the OpenAI API used by GPTs.py (DebugGPT) and Normal_GPT.py (ScoreGPT).

Same idea as UVLLM/api.py in reproduce-UVLLM-DAC-25: requests go to the repo's vLLM server
(config.yaml `vllm:` section), token usage is totalled and printed as one MEIC_USAGE line at exit.

Environment:
  MEIC_VLLM_CONFIG   config.yaml to read (default: the repo's)
  MEIC_MODEL         model id on the server (default: the one it serves)
  MEIC_TEMPERATURE   "config" (default): every call uses config.yaml's temperature, like our agent;
                     "paper": the MEIC paper's values, debug agent 0.7 and scorer 0.1 (Sec. 4).
                     (The released Normal_GPT.py scores at 0.7; the paper says 0.1.)
"""

import atexit
import itertools
import json
import logging
import os
import re
import time

import requests
import yaml

_CONFIG_PATH = os.environ.get("MEIC_VLLM_CONFIG", os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "config.yaml")))
with open(_CONFIG_PATH) as _f:
    VLLM = yaml.safe_load(_f)["vllm"]
BASE_URL = "http://{}:{}".format(VLLM.get("host", "127.0.0.1"), VLLM.get("port", 8000))
TEMPERATURE_MODE = os.environ.get("MEIC_TEMPERATURE", "config")
MIN_REPLY_TOKENS = 2048


def _served_models():
    r = requests.get(BASE_URL + "/v1/models", timeout=30)
    r.raise_for_status()
    return r.json()["data"]


_MODELS = _served_models()
MODEL = os.environ.get("MEIC_MODEL") or _MODELS[0]["id"]
MAX_MODEL_LEN = next((m.get("max_model_len") for m in _MODELS if m["id"] == MODEL), None)
_REQUEST_NO = itertools.count()   # numbers every request this process sends (see the seed in chat())

# Running usage totals, for cost comparison with the agent.
USAGE = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "latency_s": 0.0,
         "truncated_pairs": 0}


def _print_usage():
    # One machine-readable line at exit; run_meic_testset.sh collects it per bug.
    print("MEIC_USAGE " + json.dumps(USAGE), flush=True)


atexit.register(_print_usage)


class ContextOverflow(Exception):
    pass


PAPER_TEMPERATURE = {"debug": 0.7, "score": 0.1}


def temperature_for(role):
    """role: "debug" or "score"."""
    return PAPER_TEMPERATURE[role] if TEMPERATURE_MODE == "paper" else VLLM.get("temperature", 0.2)


def chat(messages, temperature):
    """One chat completion; returns the reply text (any <think> block removed)."""
    max_tokens = VLLM.get("max_new_tokens", 4096)
    if MAX_MODEL_LEN:
        n = requests.post(BASE_URL + "/tokenize", json={"model": MODEL, "messages": messages,
                          "add_generation_prompt": True}, timeout=60).json()["count"]
        if MAX_MODEL_LEN - n < MIN_REPLY_TOKENS:
            raise ContextOverflow(f"prompt is {n} tokens; context window is {MAX_MODEL_LEN}")
        max_tokens = min(max_tokens, MAX_MODEL_LEN - n)
    body = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": VLLM.get("top_p", 1.0) if temperature > 0 else 1.0,
        "repetition_penalty": VLLM.get("repetition_penalty", 1.0),
    }
    if VLLM.get("seed") is not None:
        # seed + request number: a run is still reproducible, but repeated or similar prompts
        # get new samples, as with the unseeded OpenAI API the original used.
        body["seed"] = VLLM["seed"] + next(_REQUEST_NO)
    t0 = time.perf_counter()
    r = requests.post(BASE_URL + "/v1/chat/completions", json=body, timeout=1800)
    r.raise_for_status()
    resp = r.json()
    usage = resp.get("usage") or {}
    USAGE["calls"] += 1
    USAGE["latency_s"] += time.perf_counter() - t0
    USAGE["prompt_tokens"] += usage.get("prompt_tokens", 0)
    USAGE["completion_tokens"] += usage.get("completion_tokens", 0)
    USAGE["cached_tokens"] += (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    logging.info("LLM call: temp=%s prompt=%s completion=%s finish=%s", temperature,
                 usage.get("prompt_tokens"), usage.get("completion_tokens"),
                 resp["choices"][0].get("finish_reason"))
    res = resp["choices"][0]["message"].get("content") or ""
    return re.sub(r"<think>.*?</think>\s*", "", res, flags=re.DOTALL).strip()


def chat_thread(thread, temperature):
    """Chat over a growing thread; when it no longer fits, drop the oldest question/answer pair
    (the OpenAI Assistants API truncates long threads the same way)."""
    while True:
        try:
            return chat(thread, temperature)
        except ContextOverflow:
            if len(thread) < 4:   # system + the new question alone do not fit
                raise
            del thread[1:3]
            USAGE["truncated_pairs"] += 1
