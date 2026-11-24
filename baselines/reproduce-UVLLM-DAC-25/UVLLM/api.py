import json, os, re
from utils import Logger

jsonform = {
    "module name" : "",
        "analysis" : "",
    "correct code" : [('wrong code1', 'correct code1'),('wrong code2', 'correct code2')]
}

import ast
import itertools
import logging
import time

import requests
import yaml

_CONFIG_PATH = os.environ.get("UVLLM_VLLM_CONFIG", os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "config.yaml")))
with open(_CONFIG_PATH) as _f:
    VLLM = yaml.safe_load(_f)["vllm"]
BASE_URL = "http://{}:{}".format(VLLM.get("host", "127.0.0.1"), VLLM.get("port", 8000))
TEMPERATURE_MODE = os.environ.get("UVLLM_TEMPERATURE", "config")


def _served_model():
    r = requests.get(BASE_URL + "/v1/models", timeout=30)
    r.raise_for_status()
    return r.json()["data"][0]["id"]


MODEL = os.environ.get("UVLLM_MODEL") or _served_model()
_REQUEST_NO = itertools.count()   # numbers every request this process sends (see the seed below)

# Running usage totals, for cost comparison with the agent.
USAGE = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "latency_s": 0.0}


def _print_usage():
    # One machine-readable line at exit; run_uvllm_meic.sh collects it per bug.
    print("UVLLM_USAGE " + json.dumps(USAGE), flush=True)


import atexit
atexit.register(_print_usage)


def get_completion(prompt, model="gpt-4-turbo", temperature=0.2):
    temp = temperature if TEMPERATURE_MODE == "paper" else VLLM.get("temperature", 0.2)
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "As a professional IC design engineer."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": VLLM.get("max_new_tokens", 4096),
        "temperature": temp,
        "top_p": VLLM.get("top_p", 1.0) if temp > 0 else 1.0,
        "repetition_penalty": VLLM.get("repetition_penalty", 1.0),
    }
    if VLLM.get("seed") is not None:
        # seed + request number: a run is still reproducible, but a retry of the same prompt
        # (api_syntax tries up to 5 times) gets a new sample, as the unseeded original did.
        body["seed"] = VLLM["seed"] + next(_REQUEST_NO)
    try:
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
        res = resp["choices"][0]["message"].get("content") or ""
        res = re.sub(r"<think>.*?</think>\s*", "", res, flags=re.DOTALL).strip()
        logging.info("LLM call: temp=%s prompt=%s completion=%s finish=%s", temp,
                     usage.get("prompt_tokens"), usage.get("completion_tokens"),
                     resp["choices"][0].get("finish_reason"))
        return res, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    except Exception as e:
        logging.error(f'vLLM request failed: {e}')
    return '', 0, 0


def parse_reply(text):

    fence = re.search(r"```(?:json|python)?\s*(.*?)```", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no dict in LLM reply")
    text = text[start:end + 1]
    for parse in (json.loads, ast.literal_eval):
        try:
            data = parse(text)
            if isinstance(data, dict) and "correct code" in data:
                return data
        except Exception:
            pass
    raise ValueError("unparseable LLM reply")

def api_gpt_mismatch(spec, file_path, snip, badfix:None, logger):
    with open(file_path, 'r', encoding='utf-8') as sfile:  # read-only
        content = sfile.read()

    with open(spec, 'r', encoding='utf-8') as spfile:  # read-only
        specfile = spfile.read()

    prompt = "You are an expert in RTL design and debugging. Here is a error design: \
                      Design description is {}, \n relevant source code is {}, \nmismatch signals are {}.\
                      Try to fix the one that you think is most likely to go wrong. \
                      \nPlease offer merely the fix strictly in the json form of {}, ALL pairs in one line, NO OTHER WORDS IS EXPECTED.\
                       Remember: the port definition may be wrong!".format(specfile, content, snip, jsonform)

    response = get_completion(prompt, "gpt-4-turbo", 0.9)
    logger.info(response[0])
    data = parse_reply(response[0])

    newsrc = content
    for everyOne in data["correct code"]:
        newsrc = replace_with_regex(everyOne[0], everyOne[1], newsrc)


    return  newsrc, data["correct code"]

def api_gpt_suspicious(spec, file_path, snip, badfix:None, logger):
    with open(file_path, 'r', encoding='utf-8') as sfile:  # read-only
        content = sfile.read()

    with open(spec, 'r', encoding='utf-8') as spfile:  # read-only
        specfile = spfile.read()

    prompt = "You are an expert in RTL design and debugging. Here is a error design: \
                      Design description is {}, \n relevant source code is {}, \nand suspicious error code are {}.\
                      Some of the suspicious are right. Try to fix the one that you think is most likely to go wrong. \
                      \nPlease offer merely the fix strictly in the json form of {}, ALL pairs in one line, NO OTHER WORDS IS EXPECTED.\
                       Remember: the port definition may be wrong!".format(specfile, content, snip, jsonform)

    response = get_completion(prompt, "gpt-4-turbo", 0.9)
    logger.info(response[0])
    data = parse_reply(response[0])

    newsrc = content
    for everyOne in data["correct code"]:
        newsrc = replace_with_regex(everyOne[0], everyOne[1], newsrc)

    return  newsrc, data["correct code"]

def api_syntax(file_path, snip, badfix:None, logger):

    max_try = 5
    try_api = 0
    while try_api<max_try:
        try:
            with open(file_path, 'r', encoding='utf-8') as sfile:  # read-only
                content = sfile.read()

            prompt = "You are an expert in RTL design and debugging. Here is a error design: \
                            Relevant source code is {}, \nThe compile report is {}. \
                            \nPlease offer merely the fix strictly in the json form of {}, ALL pairs in one line, NO OTHER WORDS IS EXPECTED.\
                            Remember: the port definition may be wrong!".format(content, snip, jsonform)

            response = get_completion(prompt, "gpt-4-turbo", 0.7)

            logger.info(response[0])
            data = parse_reply(response[0])
            newsrc = content
            for everyOne in data["correct code"]:
                newsrc = replace_with_regex(everyOne[0], everyOne[1], newsrc)
            break
        except Exception as e:
            try_api+=1
            logger.error("Error in claude_api.syntax, retrying...")
            logger.error(str(e))

    return  newsrc, data["correct code"]

def replace_with_regex(original, replacement, text):
    if(original == None):
        return text
    pattern = re.escape(original)
    result=re.sub(pattern, replacement, text)
    return result

