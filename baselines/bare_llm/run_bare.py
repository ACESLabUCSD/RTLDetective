"""
Non-agentic baseline: ask the model once to fix each buggy design, take the code block from its
reply, and score it with our testbench runner. No feedback, no tools, no retries, no localizer.

Usage (from the repo root, vLLM server running):
    python baselines/bare_llm/run_bare.py --seed 0 --out output/paper/bare_seed0
    python baselines/bare_llm/run_bare.py --seed 0 --out ... --designs accu alu --limit 2 --resume
    python baselines/bare_llm/run_bare.py --out ... --summary-only

Output: <out>/<design>/<bug>/{prompt.txt, reply.txt, fixed.v, result.json} and <out>/summary.csv
(one row per bug; same columns as the MEIC/UVLLM summaries where they overlap).
wall_s is the time for the model call (prompt to full reply), the whole of this method's work; the
final testbench scoring is not counted, as for the other baselines.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.benchmark_runner import run_benchmark  # noqa: E402

TESTSET = ROOT / "testset" / "MEIC_Testset"
FIELDS = ["design", "bug", "kind", "tb_all_pass", "tb_passed", "tb_total", "buggy_tb_all_pass",
          "wall_s", "llm_calls", "prompt_tokens", "completion_tokens", "cached_tokens", "llm_s",
          "finish_reason", "code_found"]

SYSTEM = "You are an expert in Verilog RTL design and debugging."
USER = """The Spec (design description) is

{spec}

The Design Code below has one or more bugs (syntax or functional). Fix them.

{code}

Reply with the complete corrected Verilog design (all modules, no testbench) in one ```verilog code block."""

_BLOCK = re.compile(r"```(?:verilog|systemverilog|v|sv)?[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_code(reply: str) -> str | None:
    """The longest fenced code block that contains a module; None if there is none."""
    reply = re.sub(r"<think>.*?</think>\s*", "", reply, flags=re.DOTALL)
    blocks = [b for b in _BLOCK.findall(reply) if re.search(r"\bmodule\b", b)]
    return max(blocks, key=len) if blocks else None


def bug_list(designs: list[str], limit: int) -> list[tuple[str, Path]]:
    bugs = []
    for ddir in sorted(p for p in TESTSET.iterdir() if p.is_dir()):
        if designs and ddir.name not in designs:
            continue
        if not (ddir / "benchmarks" / "tb.v").is_file():
            continue
        files = [f for f in sorted((ddir / "buggy").glob("*.v")) if not re.search(r"_iter\d+\.v$", f.name)]
        bugs += [(ddir.name, f) for f in (files[:limit] if limit else files)]
    return bugs


def score(src: Path, tb: Path) -> tuple[int, int, int]:
    r = run_benchmark([str(src)], str(tb), str(src), timeout=60)
    return int(r.all_pass), r.passed, r.total


def ask(url: str, model: str, cfg: dict, messages: list, seed: int | None) -> tuple[dict, float]:
    body = {"model": model, "messages": messages, "max_tokens": cfg.get("max_new_tokens", 4096),
            "temperature": cfg.get("temperature", 0.2),
            "top_p": cfg.get("top_p", 1.0) if cfg.get("temperature", 0.2) > 0 else 1.0,
            "repetition_penalty": cfg.get("repetition_penalty", 1.0)}
    if seed is not None:
        body["seed"] = seed
    t0 = time.perf_counter()
    r = requests.post(f"{url}/v1/chat/completions", json=body, timeout=3600)
    if r.status_code >= 400:
        raise RuntimeError(f"server error {r.status_code}: {r.text[:300]}")
    return r.json(), time.perf_counter() - t0


def summarize(out: Path):
    rows = [json.loads(f.read_text()) for f in sorted(out.glob("*/*/result.json"))]
    with open(out / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows({k: r.get(k) for k in FIELDS} for r in rows)
    if not rows:
        print(f"No results in {out}")
        return
    print(f"\nNon-agentic Qwen on MEIC_Testset — {len(rows)} bugs ({out})")
    for kind in ("syn", "func"):
        k = [r for r in rows if r["kind"] == kind]
        det = [r for r in k if not r["buggy_tb_all_pass"]]
        if k:
            print(f"  {kind:5s} testbench all-pass {sum(r['tb_all_pass'] for r in k)}/{len(k)}"
                  f" | excl. bugs the TB cannot detect {sum(r['tb_all_pass'] for r in det)}/{len(det)}")
    n = len(rows)
    print(f"  all   testbench all-pass {sum(r['tb_all_pass'] for r in rows)}/{n}")
    print(f"  per bug: {sum(r['wall_s'] for r in rows) / n:.1f} s, "
          f"{sum(r['prompt_tokens'] + r['completion_tokens'] for r in rows) / n:.0f} tokens; "
          f"no code block: {sum(not r['code_found'] for r in rows)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--seed", type=int, default=None, help="base seed (default: vllm: seed in config.yaml)")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--designs", nargs="*", default=[])
    ap.add_argument("--limit", type=int, default=0, help="at most N bugs per design")
    ap.add_argument("--resume", action="store_true", help="skip bugs that already have result.json")
    ap.add_argument("--summary-only", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    out = out if out.is_absolute() else ROOT / out
    if args.summary_only:
        summarize(out)
        return

    cfg = yaml.safe_load(open(args.config))["vllm"]
    base_seed = args.seed if args.seed is not None else cfg.get("seed")
    url = f"http://{cfg.get('host', '127.0.0.1')}:{cfg.get('port', 8000)}"
    model = requests.get(f"{url}/v1/models", timeout=30).json()["data"][0]["id"]

    bugs = bug_list(args.designs, args.limit)
    print(f"Bugs: {len(bugs)} | model: {model} | base seed: {base_seed} | out: {out}")
    for i, (design, vf) in enumerate(bugs):
        name = vf.stem
        d = out / design / name
        if args.resume and (d / "result.json").is_file():
            print(f"[skip] {design}/{name}")
            continue
        d.mkdir(parents=True, exist_ok=True)
        tb = TESTSET / design / "benchmarks" / "tb.v"
        spec = (TESTSET / design / "design_description.txt").read_text(errors="replace")
        user = USER.format(spec=spec.strip(), code=vf.read_text(errors="replace").strip())
        (d / "prompt.txt").write_text(user)

        t0 = time.perf_counter()
        resp, llm_s = ask(url, model, cfg, [{"role": "system", "content": SYSTEM},
                                            {"role": "user", "content": user}],
                          None if base_seed is None else base_seed + i)
        reply = resp["choices"][0]["message"].get("content") or ""
        code = extract_code(reply)
        wall_s = time.perf_counter() - t0
        (d / "reply.txt").write_text(reply)
        # No code block: the design is left unchanged (counts as not fixed unless it already passes).
        (d / "fixed.v").write_text(code if code is not None else vf.read_text(errors="replace"))

        usage = resp.get("usage") or {}
        fixed, buggy = score(d / "fixed.v", tb), score(vf, tb)
        row = dict(design=design, bug=name, kind="syn" if "_syn_" in name else "func" if "_func_" in name else "other",
                   tb_all_pass=fixed[0], tb_passed=fixed[1], tb_total=fixed[2], buggy_tb_all_pass=buggy[0],
                   wall_s=round(wall_s, 2), llm_calls=1, prompt_tokens=usage.get("prompt_tokens", 0),
                   completion_tokens=usage.get("completion_tokens", 0),
                   cached_tokens=(usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0,
                   llm_s=round(llm_s, 2), finish_reason=resp["choices"][0].get("finish_reason"),
                   code_found=int(code is not None))
        (d / "result.json").write_text(json.dumps(row, indent=1))
        print(f"[{'PASS' if fixed[0] else 'fail'}] {design}/{name}  {fixed[1]}/{fixed[2]}  {wall_s:.1f}s  "
              f"{row['prompt_tokens']}+{row['completion_tokens']} tok", flush=True)
    summarize(out)


if __name__ == "__main__":
    main()
