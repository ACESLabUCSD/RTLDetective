"""
Benchmark Runner — Compile, simulate, and score Verilog against testbenches.

Compiles a Verilog source file together with its testbench using Icarus Verilog,
runs the simulation, and parses [PASS]/[FAIL]/[SUMMARY] markers to compute a
testbench pass rate.

This is the primary evaluation metric: a fix is considered successful only when
all testbench assertions pass.

Usage:
    python tools/benchmark_runner.py <source.v> <testbench.v>
    python tools/benchmark_runner.py <source.v> <testbench.v> --json
    python tools/benchmark_runner.py <source.v> <testbench.v> --json --waveform-dir out/

With --waveform-dir, a probe module is compiled next to the design and the
testbench and dumps a VCD and turns it into a prompt table.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class BenchmarkResult:
    source_file: str
    testbench_file: str
    compiled: bool
    compile_warnings: list[str]
    compile_errors: list[str]
    simulated: bool
    sim_stdout: str
    passed: int
    failed: int
    total: int
    pass_rate: float
    all_pass: bool
    test_details: list[dict]
    error: str | None = None
    vcd_file: str | None = None
    wave_meta: dict | None = None
    wave_error: str | None = None


WAVE_TICK = "__WAVE_TICK"
MAX_VCD_BYTES = 64 * 1024 * 1024

_VVP_SCOPE_RE = re.compile(
    r'^(S_0x[0-9a-f]+) \.scope module, "([^"]+)" "([^"]+)"[^;]*?(?:, (S_0x[0-9a-f]+))?;$'
)
_VVP_PORT_RE = re.compile(r'^\s*\.port_info \d+ /(INPUT|OUTPUT|INOUT) (\d+) "([^"]+)"')
_MODULE_RE = re.compile(r"^\s*module\s+(\w+)", re.MULTILINE)


def _module_names(paths) -> set[str]:
    names = set()
    for p in paths:
        try:
            names.update(_MODULE_RE.findall(Path(p).read_text(errors="replace")))
        except OSError:
            pass
    return names


def discover_wave_meta(vvp_file: Path, source_paths, tb_path) -> dict | None:
    """Find the testbench top, the DUT instance and its ports from compiled vvp.
    """
    scopes, cur = {}, None
    for line in vvp_file.read_text(errors="replace").splitlines():
        m = _VVP_SCOPE_RE.match(line)
        if m:
            cur = m.group(1)
            scopes[cur] = {"inst": m.group(2), "type": m.group(3),
                           "parent": m.group(4), "ports": []}
            continue
        m = _VVP_PORT_RE.match(line)
        if m and cur:
            scopes[cur]["ports"].append(
                {"dir": m.group(1).lower(), "width": int(m.group(2)), "name": m.group(3)})
        elif line and not line[0].isspace():
            cur = None

    design_mods = _module_names(source_paths)
    tb_mods = _module_names([tb_path])
    roots = [sid for sid, s in scopes.items() if s["parent"] is None]
    tb_roots = [sid for sid in roots if scopes[sid]["type"] in tb_mods] or roots
    for tb_id in tb_roots:
        kids = [sid for sid, s in scopes.items()
                if s["parent"] == tb_id and s["type"] in design_mods]
        if not kids:
            continue
        dut_id = max(kids, key=lambda sid: len(scopes[sid]["ports"]))
        tb, dut = scopes[tb_id], scopes[dut_id]
        ports = dut["ports"]
        ins = [p for p in ports if p["dir"] == "input"]
        one_bit = [p["name"] for p in ins if p["width"] == 1]
        clocks = [n for n in one_bit if re.search(r"cl(oc)?k", n, re.I)]
        clocks.sort(key=lambda n: (n.lower() != "clk", len(n)))
        resets = [n for n in one_bit if re.search(r"rst|reset", n, re.I)]
        reset = resets[0] if resets else None
        return {
            "tb_top": tb["inst"],
            "dut_path": f'{tb["inst"]}.{dut["inst"]}',
            "dut_module": dut["type"],
            "ports": ports,
            "inputs": [p["name"] for p in ins],
            "outputs": [p["name"] for p in ports if p["dir"] == "output"],
            "inouts": [p["name"] for p in ports if p["dir"] == "inout"],
            "clock": clocks[0] if clocks else None,
            "other_clocks": clocks[1:],
            "reset": reset,
            "reset_active_low": bool(reset and re.search(r"(_?n|_b|_l)$|^n", reset, re.I)),
        }
    return None


def make_probe(meta: dict, vcd_path: Path) -> str:
    """Probe module: dumps the DUT (all depths) and the TB top level, and
    $strobe-s a tick at each clock edge (or each DUT input change if there is
    no clock). $strobe runs at the end of the time step, so a testbench line
    printed at the same edge comes out before that edge's tick."""
    dut, tb = meta["dut_path"], meta["tb_top"]
    if meta["clock"]:
        tick = "\n  ".join(
            f'always @(posedge {dut}.{c}) $strobe("{WAVE_TICK} %0t", $time);'
            for c in [meta["clock"]] + meta.get("other_clocks", []))
    elif meta["inputs"] or meta.get("inouts"):
        # inouts can be driven by the TB too; must match to_cycles' boundary set
        sens = " or ".join(f"{dut}.{n}" for n in meta["inputs"] + meta.get("inouts", []))
        tick = f'always @({sens}) $strobe("{WAVE_TICK} %0t", $time);'
    else:
        tick = ""
    vcd = str(vcd_path).replace("\\", "/")
    return (
        "`timescale 1ns/1ps\n"
        "module __wave_probe;\n"
        f'  initial begin $dumpfile("{vcd}"); $dumpvars(0, {dut}); $dumpvars(1, {tb}); end\n'
        f"  {tick}\n"
        "endmodule\n"
    )


BENCHMARKS_DIR = Path(__file__).resolve().parent.parent / "samples" / "benchmarks"


def find_testbench(source_file: str) -> Path | None:
    """Auto-discover testbench for a source file by stem matching."""
    stem = Path(source_file).stem
    tb = BENCHMARKS_DIR / f"{stem}_tb.v"
    return tb if tb.exists() else None


def run_benchmark(source_files: list[str],
                  testbench_file: str, 
                  source: str, 
                  timeout: int = 30,
                  waveform_dir: str | None = None) -> BenchmarkResult:
    
    """Compile and simulate multiple Verilog sources with a single testbench.
    """
    source_paths = [Path(f) for f in source_files]
    tb_path = Path(testbench_file)

    base = BenchmarkResult(
        source_file=", ".join(str(p) for p in source_paths),
        testbench_file=str(tb_path),
        compiled=False, compile_warnings=[], compile_errors=[],
        simulated=False, sim_stdout="",
        passed=0, failed=0, total=0, pass_rate=0.0, all_pass=False,
        test_details=[],
    )

    if not shutil.which("iverilog"):
        base.error = "iverilog not found — install Icarus Verilog"
        return base

    with tempfile.TemporaryDirectory() as tmpdir:
        vvp_file = Path(tmpdir) / "sim.vvp"

        # --- Compile all sources together ---
        comp = subprocess.run(
            ["iverilog", "-g2012", "-Wall", "-o", str(vvp_file), "-I", str(source)] + [str(p) for p in source_paths] + [str(tb_path)],
            capture_output=True, text=True, timeout=timeout,
        )

        for line in (comp.stderr or "").strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            if "error" in line.lower():
                base.compile_errors.append(line)
            elif "warning" in line.lower():
                base.compile_warnings.append(line)

        if comp.returncode != 0:
            base.error = "Compilation failed"
            base.compiled = False
            base.sim_stdout = comp.stderr
            return base
        base.compiled = True

        sim_vvp = vvp_file
        tmp_vcd = Path(tmpdir) / "dut.vcd"
        if waveform_dir:
            meta = discover_wave_meta(vvp_file, source_paths, tb_path)
            if meta is None:
                base.wave_error = "could not find the DUT instance under the testbench top"
            else:
                probe = Path(tmpdir) / "__wave_probe.v"
                probe.write_text(make_probe(meta, tmp_vcd))
                wave_vvp = Path(tmpdir) / "sim_wave.vvp"
                comp2 = subprocess.run(
                    ["iverilog", "-g2012", "-o", str(wave_vvp), "-I", str(source)]
                    + [str(p) for p in source_paths] + [str(tb_path), str(probe)],
                    capture_output=True, text=True, timeout=timeout,
                )
                if comp2.returncode == 0:
                    sim_vvp = wave_vvp
                    base.wave_meta = meta
                else:
                    base.wave_error = "probe compile failed: " + (comp2.stderr or "").strip()[:300]

        # --- Simulate ---
        try:
            sim = subprocess.run(
                ["vvp", str(sim_vvp)],
                capture_output=True, text=True, timeout=timeout, #cwd=tmpdir,
            )
        except subprocess.TimeoutExpired:
            base.error = "Simulation timed out"
            base.simulated = False
            return base

        base.simulated = True

        for vcd in Path(".").glob("*.vcd"):
            vcd.unlink()

        # --- Parse results (tick lines come from the waveform probe) ---
        ticks, last_tick, kept = 0, None, []
        for line in sim.stdout.split("\n"):
            if line.startswith(WAVE_TICK):
                if line != last_tick:   # tick lines carry $time: same time = one tick
                    ticks += 1
                last_tick = line
                continue
            kept.append(line)
            m_pass = re.match(r"\[PASS\]\s*(.*)", line)
            m_fail = re.match(r"\[FAIL\]\s*(.*)", line)
            extra = {"tick": ticks} if base.wave_meta else {}
            if m_pass:
                base.passed += 1
                base.test_details.append({"status": "pass", "message": m_pass.group(1), **extra})
            elif m_fail:
                base.failed += 1
                base.test_details.append({"status": "fail", "message": m_fail.group(1), **extra})
        base.sim_stdout = "\n".join(kept)

        if base.wave_meta:
            base.wave_meta["ticks"] = ticks
            if not tmp_vcd.exists():
                base.wave_error = "simulation produced no VCD"
            elif tmp_vcd.stat().st_size > MAX_VCD_BYTES:
                base.wave_error = f"VCD too large ({tmp_vcd.stat().st_size} bytes), skipped"
            else:
                out_dir = Path(waveform_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                dest = out_dir / f"{source_paths[0].stem}.vcd"
                shutil.copyfile(tmp_vcd, dest)
                base.vcd_file = str(dest)

        base.total = base.passed + base.failed
        base.pass_rate = base.passed / base.total if base.total > 0 else 0.0
        base.all_pass = (base.failed == 0 and base.total > 0)

        return base


def format_result(r: BenchmarkResult, use_json: bool = False) -> str:
    """Format a BenchmarkResult for display."""
    if use_json:
        return json.dumps(asdict(r), indent=2)

    lines = []
    lines.append(f"[BENCHMARK] {Path(r.testbench_file).name}")
    lines.append(f"  Compiled:    {'yes' if r.compiled else 'NO'}")
    if r.compile_errors:
        for e in r.compile_errors:
            lines.append(f"    ERROR: {e}")
    if r.compile_warnings:
        for w in r.compile_warnings:
            lines.append(f"    WARN:  {w}")
    lines.append(f"  Simulated:   {'yes' if r.simulated else 'NO'}")
    if r.error:
        lines.append(f"  Error:       {r.error}")
    lines.append(f"  Tests:       {r.total}")
    lines.append(f"  Passed:      {r.passed}")
    lines.append(f"  Failed:      {r.failed}")
    lines.append(f"  Pass rate:   {r.pass_rate:.1%}")
    lines.append(f"  All pass:    {'YES' if r.all_pass else 'NO'}")

    if r.failed > 0:
        lines.append("  Failed tests:")
        for td in r.test_details:
            if td["status"] == "fail":
                lines.append(f"    - {td['message']}")
    return "\n".join(lines)


def collect_verilog_files(path: str) -> list[str]:
    """Recursively collect all .v files from a folder or return a single file."""
    p = Path(path).resolve()
    if p.is_file() and (p.suffix == ".v" or p.suffix == ".sv"):
        return [str(p)]
    elif p.is_dir():
        return [str(f) for ext in ("*.v", "*.sv") for f in p.rglob(ext)]
    else:
        return []


def main():
    parser = argparse.ArgumentParser(description="Verilog Benchmark Runner")
    parser.add_argument("source", help="Verilog source file")
    parser.add_argument("testbench", nargs="?", help="Testbench file (auto-detected if omitted)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout in seconds")
    parser.add_argument("--waveform-dir", default=None, help="Also dump a VCD of the DUT into this directory")
    args = parser.parse_args()

    files = collect_verilog_files(args.source)
    if not files:
        print(f"Error: no .v files found at {args.source}", file=sys.stderr)
        sys.exit(1)

    tb = args.testbench
    if not tb:
        found = find_testbench(args.source)
        if found:
            tb = str(found)
        else:
            print(f"Error: no testbench found for {args.source}", file=sys.stderr)
            print(f"Place a testbench at samples/benchmarks/{Path(args.source).stem}_tb.v",
                  file=sys.stderr)
            sys.exit(1)

    print(f"Running benchmark for {files} with testbench {tb}...")
    result = run_benchmark(files, tb, args.source, timeout=args.timeout, waveform_dir=args.waveform_dir)
    print(format_result(result, use_json=args.json))
    sys.exit(0 if result.all_pass else 1)


if __name__ == "__main__":
    main()
