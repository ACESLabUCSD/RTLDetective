"""
Shared vLLM server for the Verilog Debug Agent.

Instead of loading the model inside every agent process, one
`vllm serve <model> --enable-prefix-caching` process runs in the background
and every agent run talks to it over HTTP (OpenAI-compatible API). The KV
cache — including the prefix cache for the shared system prompt — then
survives across agent processes, so a bash loop over files keeps hitting it.

The first agent that needs the server starts it (detached, so it outlives
that agent); later agents find it already running. A lock file stops two
agents from starting it at the same time.

    python -m agent.backends.vllm_server start     # start (or confirm) the server
    python -m agent.backends.vllm_server status
    python -m agent.backends.vllm_server stop

Settings come from the `vllm:` and `local_model:` sections of config.yaml.
"""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
import requests
from agent.backends.config import PROJECT_ROOT, load_config_section

logger = logging.getLogger(__name__)

RUN_DIR = PROJECT_ROOT / "logs"
LOG_FILE = RUN_DIR / "vllm_server.log"
PID_FILE = RUN_DIR / "vllm_server.pid"
LOCK_FILE = RUN_DIR / "vllm_server.lock"

DEFAULT_MODEL = "Qwen/Qwen3-Coder-30B-A3B-Instruct"


def load_settings() -> dict:
    """Merge the config.yaml sections the server needs."""
    lcfg = load_config_section("local_model")
    vcfg = load_config_section("vllm")
    return {
        "host": vcfg.get("host", "127.0.0.1"),
        "port": int(vcfg.get("port", 8000)),
        "startup_timeout_s": int(vcfg.get("startup_timeout_s", 1800)),
        "tensor_parallel_size": vcfg.get("tensor_parallel_size"),
        "gpu_memory_utilization": vcfg.get("gpu_memory_utilization", 0.85),
        "max_model_len": vcfg.get("max_model_len", 32768),
        "enforce_eager": vcfg.get("enforce_eager", False),
        "cache_dir": lcfg.get("cache_dir"),
        "torch_dtype": lcfg.get("torch_dtype", "auto"),
        "gpu_ids": lcfg.get("gpu_ids"),
    }


def base_url(settings: dict | None = None) -> str:
    s = settings or load_settings()
    return f"http://{s['host']}:{s['port']}"


def served_models(url: str, timeout: float = 3.0) -> list[dict] | None:
    """Models the server at `url` serves, or None if nothing is listening."""
    try:
        if requests.get(f"{url}/health", timeout=timeout).status_code != 200:
            return None
        r = requests.get(f"{url}/v1/models", timeout=timeout)
        r.raise_for_status()
        return r.json().get("data", [])
    except requests.RequestException:
        return None


def _tensor_parallel(settings: dict) -> int:
    """Configured TP, else the largest power of two <= number of gpu_ids."""
    if settings["tensor_parallel_size"]:
        return int(settings["tensor_parallel_size"])
    ids = str(settings["gpu_ids"] or os.environ.get("CUDA_VISIBLE_DEVICES", "")).strip()
    n = len([g for g in ids.split(",") if g.strip()]) if ids else 1
    tp = 1
    while tp * 2 <= n:
        tp *= 2
    return tp


def _vllm_executable() -> str:
    if os.environ.get("VLLM_BIN"):              # override, e.g. another vLLM install
        return os.environ["VLLM_BIN"]
    local = Path(sys.executable).parent / "vllm"
    return str(local) if local.exists() else "vllm"


def build_command(model: str, settings: dict) -> list[str]:
    cmd = [
        _vllm_executable(), "serve", model,
        "--enable-prefix-caching",
        "--enable-prompt-tokens-details",       # reports cached prompt tokens per request
        "--host", settings["host"],
        "--port", str(settings["port"]),
        "--served-model-name", model,
        "--tensor-parallel-size", str(_tensor_parallel(settings)),
        "--gpu-memory-utilization", str(settings["gpu_memory_utilization"]),
        "--dtype", str(settings["torch_dtype"]),
        "--trust-remote-code",
    ]
    if settings["max_model_len"]:
        cmd += ["--max-model-len", str(settings["max_model_len"])]
    if settings["cache_dir"]:
        cmd += ["--download-dir", str(Path(settings["cache_dir"]).expanduser())]
    if settings["enforce_eager"]:
        cmd.append("--enforce-eager")
    return cmd


def _check_model(models: list[dict], model: str, url: str) -> None:
    ids = [m.get("id") for m in models]
    if model not in ids:
        raise RuntimeError(
            f"vLLM server at {url} serves {ids}, not {model}. "
            "Stop it (python -m agent.backends.vllm_server stop) or set another vllm.port in config.yaml."
        )


def _files(s: dict) -> tuple[Path, Path, Path]:
    """Log, pid and lock file of the server on s["port"] (a second server, e.g. the localizer's, gets its own)."""
    tag = "" if int(s["port"]) == 8000 else f"_{s['port']}"
    return (RUN_DIR / f"vllm_server{tag}.log", RUN_DIR / f"vllm_server{tag}.pid", RUN_DIR / f"vllm_server{tag}.lock")


def _log_tail(n: int = 40, log_file: Path = LOG_FILE) -> str:
    try:
        return "\n".join(log_file.read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return "(no server log)"


def ensure_server(model: str = DEFAULT_MODEL, settings: dict | None = None) -> str:
    """Return the base URL of a running server for `model`, starting one if needed."""
    return start_server(model, settings)[0]


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def start_server(model: str = DEFAULT_MODEL, settings: dict | None = None) -> tuple[str, bool]:
    s = settings or load_settings()
    url = base_url(s)
    models = served_models(url)
    if models is not None:
        _check_model(models, model, url)
        return url, False
    LOG_FILE, PID_FILE, LOCK_FILE = _files(s)

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)          # one starter at a time
        models = served_models(url)               # someone else may have started it
        if models is not None:
            _check_model(models, model, url)
            return url, False

        proc, pid = None, None
        if PID_FILE.exists():                     # started earlier, still loading?
            try:
                pid = int(PID_FILE.read_text().strip())
            except ValueError:
                pid = None
            if pid and _pid_alive(pid):
                logger.info("vLLM server (pid %d) is still starting; waiting...", pid)
            else:
                pid = None
                PID_FILE.unlink(missing_ok=True)
        if not PID_FILE.exists():
            cmd = build_command(model, s)
            env = dict(os.environ)
            if s["gpu_ids"]:
                env["CUDA_VISIBLE_DEVICES"] = str(s["gpu_ids"])
            env.setdefault("VLLM_USE_FLASHINFER_SAMPLER", "0")
            logger.info("Starting vLLM server: %s", " ".join(cmd))
            logger.info("Server log: %s", LOG_FILE)
            with open(LOG_FILE, "ab") as log:
                proc = subprocess.Popen(
                    cmd, stdout=log, stderr=subprocess.STDOUT, env=env,
                    cwd=str(PROJECT_ROOT), start_new_session=True,  # outlives this agent
                )
            PID_FILE.write_text(str(proc.pid))
            pid = proc.pid

        deadline = time.time() + s["startup_timeout_s"]
        while time.time() < deadline:
            models = served_models(url)
            if models is not None:
                _check_model(models, model, url)
                logger.info("vLLM server ready at %s", url)
                return url, proc is not None
            dead = (proc.poll() is not None) if proc is not None else not (pid and _pid_alive(pid))
            if dead:
                PID_FILE.unlink(missing_ok=True)
                code = proc.returncode if proc is not None else "?"
                raise RuntimeError(f"vLLM server exited (code {code}) before it was ready. "
                                   f"Last lines of {LOG_FILE}:\n{_log_tail(log_file=LOG_FILE)}")
            time.sleep(5)
        raise TimeoutError(f"vLLM server not ready after {s['startup_timeout_s']}s.\n{_log_tail(log_file=LOG_FILE)}")


def stop_server() -> bool:
    """Stop the server started by ensure_server (whole process group)."""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.killpg(pid, signal.SIGTERM)
        for _ in range(60):
            os.kill(pid, 0)
            time.sleep(1)
        os.killpg(pid, signal.SIGKILL)
    except (ValueError, ProcessLookupError, PermissionError):
        pass
    PID_FILE.unlink(missing_ok=True)
    return True


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Manage the shared vLLM server")
    ap.add_argument("action", choices=["start", "status", "stop"])
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help="model ID or agent alias (e.g. qwen-coder-30b)")
    args = ap.parse_args()
    # Imported lazily: llm_client -> vllm_client -> vllm_server would be circular.
    from agent.backends.llm_client import _bare_model_id
    model = _bare_model_id(args.model)
    s = load_settings()
    url = base_url(s)
    if args.action == "start":
        # Last stdout line: "started <url>" or "running <url>" (for scripts).
        try:
            url, started = start_server(model, s)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"{'started' if started else 'running'} {url}")
    elif args.action == "status":
        # Exit code 0 = up, 1 = down.
        models = served_models(url)
        pid = PID_FILE.read_text().strip() if PID_FILE.exists() else "-"
        print(f"{url}: " + ("down" if models is None else
                            "up, serving " + ", ".join(m["id"] for m in models)) + f" (pid {pid})")
        sys.exit(0 if models is not None else 1)
    else:
        print("stopped" if stop_server() else "no server started by this project (no pid file)")


if __name__ == "__main__":
    main()
