"""
Verilog Test Runner.

Compiles and runs Verilog simulations using Icarus Verilog (iverilog/vvp)
or Verilator if available. Auto-generates a basic testbench if none provided.

Usage:
    python tools/test_runner.py <file.v> [--testbench <tb.v>] [--timeout 30]
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path


def find_simulator() -> tuple[str, str]:
    """Detect which simulator is available."""
    if shutil.which("iverilog"):
        return "iverilog", "Icarus Verilog"
    if shutil.which("verilator"):
        return "verilator", "Verilator"
    return "", "none"


def extract_module_info(source: str) -> dict:
    """Extract module name and ports from Verilog source."""
    import re
    m = re.search(r"module\s+(\w+)\s*\(", source)
    if not m:
        return {"name": "unknown", "ports": []}

    module_name = m.group(1)
    paren_start = source.find("(", m.start())
    paren_end = source.find(")", paren_start)
    port_section = source[paren_start+1:paren_end]

    ports = []
    for port_decl in port_section.split(","):
        port_decl = port_decl.strip()
        if not port_decl:
            continue
        parts = port_decl.split()
        direction = "input"
        width = ""
        name = parts[-1] if parts else ""

        for i, p in enumerate(parts):
            if p in ("input", "output", "inout"):
                direction = p
            elif p == "wire" or p == "reg":
                continue
            elif p.startswith("["):
                width = p
                if not p.endswith("]") and i + 1 < len(parts):
                    width += parts[i+1]

        ports.append({"direction": direction, "width": width, "name": name})

    return {"name": module_name, "ports": ports}


def generate_basic_testbench(source: str, module_file: str) -> str:
    """Auto-generate a simple testbench for the module."""
    info = extract_module_info(source)
    module_name = info["name"]
    ports = info["ports"]

    declarations = []
    connections = []
    for port in ports:
        w = port["width"] if port["width"] else ""
        name = port["name"]
        if port["direction"] == "input":
            declarations.append(f"    reg {w} {name};")
        else:
            declarations.append(f"    wire {w} {name};")
        connections.append(f"        .{name}({name})")

    decl_str = "\n".join(declarations)
    conn_str = ",\n".join(connections)

    has_clk = any(p["name"] in ("clk", "clock") for p in ports)
    has_rst = any(p["name"] in ("rst", "reset", "rst_n", "reset_n") for p in ports)

    clk_gen = ""
    if has_clk:
        clk_gen = textwrap.dedent("""\
            initial clk = 0;
            always #5 clk = ~clk;
        """)

    rst_seq = ""
    if has_rst:
        rst_name = next(p["name"] for p in ports if p["name"] in ("rst", "reset", "rst_n", "reset_n"))
        active_low = rst_name.endswith("_n")
        if active_low:
            rst_seq = f"        {rst_name} = 0; #20; {rst_name} = 1; #10;"
        else:
            rst_seq = f"        {rst_name} = 1; #20; {rst_name} = 0; #10;"

    return textwrap.dedent(f"""\
    `timescale 1ns/1ps
    module tb_{module_name};

    {decl_str}

        {module_name} uut (
    {conn_str}
        );

        {clk_gen}
        initial begin
            $dumpfile("dump.vcd");
            $dumpvars(0, tb_{module_name});
    {rst_seq}
            #200;
            $display("[TB] Simulation completed without timeout.");
            $finish;
        end

        initial begin
            #10000;
            $display("[TB] ERROR: Simulation timed out!");
            $finish;
        end

    endmodule
    """)


def run_icarus(verilog_file: str, testbench_file: str, timeout: int) -> dict:
    """Compile and run using Icarus Verilog."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_file = Path(tmpdir) / "sim.vvp"
        vcd_file = Path(tmpdir) / "dump.vcd"

        compile_cmd = [
            "iverilog", "-Wall", "-o", str(out_file),
            testbench_file, verilog_file,
        ]

        result = {"compile": {}, "simulate": {}, "warnings": [], "errors": []}

        comp = subprocess.run(
            compile_cmd, capture_output=True, text=True, timeout=timeout,
        )
        result["compile"]["returncode"] = comp.returncode
        result["compile"]["stdout"] = comp.stdout
        result["compile"]["stderr"] = comp.stderr

        if comp.stderr:
            for line in comp.stderr.strip().split("\n"):
                if "warning" in line.lower():
                    result["warnings"].append(line.strip())
                elif "error" in line.lower():
                    result["errors"].append(line.strip())

        if comp.returncode != 0:
            return result

        sim = subprocess.run(
            ["vvp", str(out_file)],
            capture_output=True, text=True, timeout=timeout,
            cwd=tmpdir,
        )
        result["simulate"]["returncode"] = sim.returncode
        result["simulate"]["stdout"] = sim.stdout
        result["simulate"]["stderr"] = sim.stderr

        if sim.stderr:
            for line in sim.stderr.strip().split("\n"):
                if "warning" in line.lower():
                    result["warnings"].append(line.strip())
                elif "error" in line.lower():
                    result["errors"].append(line.strip())

        result["vcd_generated"] = vcd_file.exists()
        return result


def run_verilator_lint(verilog_file: str) -> dict:
    """Run Verilator in lint-only mode."""
    cmd = ["verilator", "--lint-only", "-Wall", verilog_file]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def main():
    parser = argparse.ArgumentParser(description="Verilog Test Runner")
    parser.add_argument("file", help="Verilog source file")
    parser.add_argument("--testbench", "-t", help="Testbench file (auto-generated if omitted)")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout in seconds")
    parser.add_argument("--lint-only", action="store_true", help="Only run lint (no simulation)")
    args = parser.parse_args()

    source_path = Path(args.file)
    if not source_path.exists():
        print(f"Error: file not found: {source_path}", file=sys.stderr)
        sys.exit(1)

    source = source_path.read_text(encoding="utf-8")
    sim_name, sim_desc = find_simulator()

    print(f"[TEST RUNNER] File: {source_path.name}")
    print(f"[TEST RUNNER] Simulator: {sim_desc}")

    if args.lint_only and sim_name == "verilator":
        print("[TEST RUNNER] Running Verilator lint...")
        result = run_verilator_lint(args.file)
        print(result.get("stderr", ""))
        sys.exit(result["returncode"])

    if sim_name == "iverilog":
        tb_file = args.testbench
        tmp_tb = None
        if not tb_file:
            print("[TEST RUNNER] No testbench provided — auto-generating...")
            tb_content = generate_basic_testbench(source, args.file)
            tmp_tb = tempfile.NamedTemporaryFile(
                mode="w", suffix="_tb.v", delete=False
            )
            tmp_tb.write(tb_content)
            tmp_tb.flush()
            tb_file = tmp_tb.name
            print(f"[TEST RUNNER] Auto-generated testbench: {tb_file}")

        try:
            result = run_icarus(args.file, tb_file, args.timeout)

            print(f"\n[COMPILE] Return code: {result['compile']['returncode']}")
            if result["compile"]["stderr"]:
                print(result["compile"]["stderr"])

            if result["compile"]["returncode"] == 0:
                print(f"[SIMULATE] Return code: {result['simulate']['returncode']}")
                if result["simulate"]["stdout"]:
                    print(result["simulate"]["stdout"])
                if result["simulate"]["stderr"]:
                    print(result["simulate"]["stderr"])

            if result["warnings"]:
                print(f"\n[WARNINGS] ({len(result['warnings'])})")
                for w in result["warnings"]:
                    print(f"  {w}")

            if result["errors"]:
                print(f"\n[ERRORS] ({len(result['errors'])})")
                for e in result["errors"]:
                    print(f"  {e}")

            sys.exit(1 if result["errors"] else 0)

        finally:
            if tmp_tb:
                Path(tmp_tb.name).unlink(missing_ok=True)
    else:
        print("[TEST RUNNER] No supported simulator found (install iverilog or verilator).")
        print("[TEST RUNNER] Falling back to static analysis only.")
        from tools.static_analyzer import VerilogStaticAnalyzer
        analyzer = VerilogStaticAnalyzer(source, str(source_path))
        findings = analyzer.analyze()
        if findings:
            for f in findings:
                print(f"  Line {f.line}: [{f.severity}] {f.message}")
        else:
            print("  No static issues found.")
        sys.exit(0)


if __name__ == "__main__":
    main()
