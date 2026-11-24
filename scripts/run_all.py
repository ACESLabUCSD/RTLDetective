"""
Batch Runner — Run the debug agent across all sample buggy files.

Pairs each buggy file with its testbench (from samples/benchmarks/) and produces
a combined evaluation report with testbench pass rates.

Usage:
    python scripts/run_all.py [--model qwen-coder-30b] [--backend hf|vllm|openrouter] [--verbose]
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.debug_agent import run_agent, DEFAULT_MAX_LOOPS, find_testbench
from agent.backends.llm_client import ALL_MODELS, BACKENDS, DEFAULT_BACKEND, resolve_model_name

BUGGY_DIR = PROJECT_ROOT / "samples" / "buggy"
OUTPUT_DIR = PROJECT_ROOT / "output"


def main():
    parser = argparse.ArgumentParser(description="Batch debug agent runner")
    parser.add_argument(
        "--model", "-m", default="qwen-coder-30b",
        help="LLM model to use",
    )
    parser.add_argument(
        "--backend", "-b", choices=BACKENDS, default=DEFAULT_BACKEND,
        help=f"Inference backend: openrouter, hf, vllm (default: {DEFAULT_BACKEND})",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--max-loops", "-L", type=int, default=DEFAULT_MAX_LOOPS,
        help=f"Max debugging iterations per file (default: {DEFAULT_MAX_LOOPS})",
    )
    parser.add_argument(
        "--files", nargs="*",
        help="Specific files to process (default: all in samples/buggy/)",
    )
    args = parser.parse_args()

    model = resolve_model_name(args.model)

    if args.files:
        buggy_files = [Path(f) for f in args.files]
    else:
        buggy_files = sorted(BUGGY_DIR.glob("*.v"))

    if not buggy_files:
        print("No Verilog files found to process.")
        sys.exit(1)

    print(f"{'=' * 60}")
    print(f"  BATCH DEBUG RUN — {len(buggy_files)} file(s)")
    print(f"  Model: {model}")
    print(f"{'=' * 60}\n")

    results = []
    for vfile in buggy_files:
        print(f"\n--- Processing: {vfile.name} ---")
        tb = find_testbench(str(vfile))
        if tb:
            print(f"    Testbench: {Path(tb).name}")
        else:
            print("    Testbench: not found (static-analysis only)")

        try:
            report = run_agent(
                verilog_file=str(vfile),
                model=model,
                backend=args.backend,
                max_loops=args.max_loops,
                verbose=args.verbose,
            )
            final = report.get("final_analysis", {})
            evaluation = report.get("evaluation", {})
            results.append({
                "file": vfile.name,
                "status": "success",
                "bugs_found": final.get("summary", {}).get("total_bugs", 0),
                "iterations": report.get("iterations_used", 1),
                "converged": report.get("converged", False),
                "initial_pass_rate": evaluation.get("initial_pass_rate"),
                "final_pass_rate": evaluation.get("final_pass_rate"),
                "all_pass": evaluation.get("all_pass"),
            })
        except Exception as e:
            print(f"    ERROR: {e}")
            results.append({
                "file": vfile.name,
                "status": "error",
                "error": str(e),
            })

    # Summary
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_path = OUTPUT_DIR / f"batch_summary_{timestamp}.json"

    total = len(results)
    success = sum(1 for r in results if r["status"] == "success")
    total_bugs = sum(r.get("bugs_found", 0) for r in results)

    tb_results = [r for r in results if r.get("final_pass_rate") is not None]
    avg_pass_rate = (
        sum(r["final_pass_rate"] for r in tb_results) / len(tb_results)
        if tb_results else None
    )
    all_pass_count = sum(1 for r in tb_results if r.get("all_pass"))

    summary = {
        "timestamp": datetime.now().isoformat(),
        "model": model,
        "files_processed": total,
        "successful": success,
        "failed": total - success,
        "total_bugs_found": total_bugs,
        "benchmarks_with_testbench": len(tb_results),
        "benchmarks_all_pass": all_pass_count,
        "average_pass_rate": round(avg_pass_rate, 3) if avg_pass_rate is not None else None,
        "results": results,
    }

    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n{'=' * 60}")
    print(f"  BATCH SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Files:            {total}")
    print(f"  Successful:       {success}")
    print(f"  Failed:           {total - success}")
    print(f"  Total bugs:       {total_bugs}")
    if tb_results:
        print(f"\n  --- Testbench Evaluation ---")
        print(f"  Benchmarks run:   {len(tb_results)}")
        print(f"  All tests pass:   {all_pass_count}/{len(tb_results)}")
        if avg_pass_rate is not None:
            print(f"  Avg pass rate:    {avg_pass_rate:.1%}")
        print()
        for r in results:
            if r.get("final_pass_rate") is not None:
                init = r.get("initial_pass_rate", 0)
                final = r["final_pass_rate"]
                status = "PASS" if r.get("all_pass") else "FAIL"
                print(f"    [{status}] {r['file']:25s}  "
                      f"{init:.0%} -> {final:.0%}  "
                      f"({r.get('iterations', '?')} iters)")
    print(f"\n  Report: {summary_path}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
