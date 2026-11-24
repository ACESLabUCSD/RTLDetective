"""
Benchmark Evaluation — Score agent output by testbench pass rate.

Reads debug report JSON files and their associated fixed Verilog, then runs
(or reads cached) testbench results. The primary metric is the testbench pass
rate: a fix is correct only when ALL assertions pass (100%).

Usage:
    # Evaluate a single report
    python scripts/evaluate.py output/report_counter_4bit_*.json

    # Evaluate all reports in output/
    python scripts/evaluate.py --report-dir output/

    # Re-run testbenches on the fixed files (instead of reading cached results)
    python scripts/evaluate.py --rerun
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.benchmark_runner import run_benchmark, find_testbench, format_result

BENCHMARKS_DIR = PROJECT_ROOT / "samples" / "benchmarks"


def evaluate_report(report_path: Path, rerun: bool = False) -> dict:
    """Evaluate a single report using testbench pass rate."""
    with open(report_path) as f:
        report = json.load(f)

    source_file = Path(report.get("source_file", ""))
    stem = source_file.stem
    eval_info = report.get("evaluation", {})

    result = {
        "file": stem,
        "model": report.get("model", "unknown"),
        "iterations": report.get("iterations_used", 0),
        "converged": report.get("converged", False),
    }

    # Try to find a testbench
    tb_file = eval_info.get("testbench_file")
    if not tb_file:
        tb_path = find_testbench(str(source_file))
        tb_file = str(tb_path) if tb_path else None

    if not tb_file or not Path(tb_file).exists():
        result["error"] = "No testbench found"
        return result

    result["testbench"] = Path(tb_file).name

    if rerun:
        fixed_path = source_file.with_stem(stem + "_fixed")
        if not fixed_path.exists():
            fixed_path = source_file.parent / f"{stem}_fixed.v"
        if not fixed_path.exists():
            result["error"] = f"Fixed file not found: {stem}_fixed.v"
            return result

        # Run testbench on fixed code
        bench = run_benchmark(str(fixed_path), tb_file)
        result["initial_pass_rate"] = None
        result["final_pass_rate"] = bench.pass_rate
        result["final_passed"] = bench.passed
        result["final_failed"] = bench.failed
        result["final_total"] = bench.total
        result["all_pass"] = bench.all_pass
        if not bench.compiled:
            result["compile_error"] = True
        if bench.failed > 0:
            result["failed_tests"] = [
                td["message"] for td in bench.test_details if td["status"] == "fail"
            ]

        # Also run on buggy code for comparison
        buggy_path = PROJECT_ROOT / "samples" / "buggy" / f"{stem}.v"
        if buggy_path.exists():
            buggy_bench = run_benchmark(str(buggy_path), tb_file)
            result["initial_pass_rate"] = buggy_bench.pass_rate
    else:
        result["initial_pass_rate"] = eval_info.get("initial_pass_rate")
        result["final_pass_rate"] = eval_info.get("final_pass_rate")
        result["final_passed"] = eval_info.get("final_passed")
        result["final_failed"] = eval_info.get("final_failed")
        result["final_total"] = eval_info.get("final_total")
        result["all_pass"] = eval_info.get("all_pass", False)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate debug agent by testbench pass rate",
    )
    parser.add_argument("reports", nargs="*", help="Report JSON files")
    parser.add_argument("--report-dir", help="Directory of report files")
    parser.add_argument("--rerun", action="store_true",
                        help="Re-run testbenches on fixed files instead of using cached results")
    args = parser.parse_args()

    report_files = []
    if args.reports:
        report_files = [Path(r) for r in args.reports]
    elif args.report_dir:
        report_files = sorted(Path(args.report_dir).glob("report_*.json"))
    else:
        report_files = sorted((PROJECT_ROOT / "output").glob("report_*.json"))

    if not report_files:
        print("No report files found.")
        sys.exit(1)

    print(f"Evaluating {len(report_files)} report(s)...\n")

    results = []
    for rp in report_files:
        result = evaluate_report(rp, rerun=args.rerun)
        results.append(result)

        if "error" in result:
            print(f"  {result['file']}: {result['error']}")
        else:
            init = result.get("initial_pass_rate")
            final = result.get("final_pass_rate")
            passed = result.get("final_passed", "?")
            total = result.get("final_total", "?")
            all_ok = result.get("all_pass", False)
            status = "PASS" if all_ok else "FAIL"

            init_str = f"{init:.0%}" if init is not None else "N/A"
            final_str = f"{final:.0%}" if final is not None else "N/A"

            print(f"  [{status}] {result['file']:25s}  "
                  f"buggy={init_str}  fixed={final_str}  "
                  f"({passed}/{total} tests)  "
                  f"iters={result.get('iterations', '?')}")

            if result.get("failed_tests"):
                for ft in result["failed_tests"][:5]:
                    print(f"         FAIL: {ft}")
                if len(result.get("failed_tests", [])) > 5:
                    print(f"         ... and {len(result['failed_tests']) - 5} more")
        print()

    # Aggregate
    valid = [r for r in results if "error" not in r and r.get("final_pass_rate") is not None]
    if valid:
        avg_init = sum(r.get("initial_pass_rate", 0) or 0 for r in valid) / len(valid)
        avg_final = sum(r["final_pass_rate"] for r in valid) / len(valid)
        all_pass_count = sum(1 for r in valid if r.get("all_pass"))

        print(f"{'=' * 60}")
        print(f"  BENCHMARK EVALUATION SUMMARY ({len(valid)} files)")
        print(f"{'=' * 60}")
        print(f"  Avg buggy pass rate:   {avg_init:.1%}")
        print(f"  Avg fixed pass rate:   {avg_final:.1%}")
        print(f"  Improvement:           {avg_final - avg_init:+.1%}")
        print(f"  All tests pass:        {all_pass_count}/{len(valid)}")
        print(f"  Solve rate:            {all_pass_count / len(valid):.1%}")
        print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
