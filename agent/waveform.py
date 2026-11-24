"""
Waveform tables for the debug agent.

Turns the VCD written by tools/benchmark_runner.py into a text table for the LLM prompt:

    parse_vcd          VCD file -> signals + value changes
    to_cycles          sample every signal once per clock cycle (just before
                       each posedge of any DUT clock), or once per input
                       change for combinational designs
    select_signals     DUT ports + the outputs' dependency cone (given by the
                       caller, from VerilogTracer.output_cone)
    transitions_table  keep only cycles where something changed, as old→new

Also: reconstruct_dense rebuilds the per-cycle values from the transition table
text, to check that nothing is lost except where it was summarized.

Only what the testbench printed ([PASS]/[FAIL] lines) goes into the TB column.
The testbench source is never included.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from pathlib import Path

ARROW = "→"
DASH = "–"


# ---------------------------------------------------------------------------
# 1. VCD parsing
# ---------------------------------------------------------------------------

@dataclass
class Vcd:
    timescale: str
    end_time: int
    # full hierarchical name -> (id, width, var type)
    vars: dict[str, tuple[str, int, str]]
    # id -> sorted [(time, value)] (value: binary string padded to width, or real as text)
    changes: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    _times: dict[str, list[int]] = field(default_factory=dict, repr=False)

    def times(self, vid: str) -> list[int]:
        if vid not in self._times:
            self._times[vid] = [t for t, _ in self.changes.get(vid, [])]
        return self._times[vid]

    def value_before(self, vid: str, t: int, width: int) -> str:
        """Last value strictly before time t (what a flop sees at an edge at t)."""
        i = bisect.bisect_left(self.times(vid), t) - 1
        return self.changes[vid][i][1] if i >= 0 else "x" * width

    def value_at(self, vid: str, t: int, width: int) -> str:
        """Last value at or before time t."""
        i = bisect.bisect_right(self.times(vid), t) - 1
        return self.changes[vid][i][1] if i >= 0 else "x" * width


def _norm(raw: str, width: int) -> str:
    raw = raw.lower()
    if len(raw) < width:
        pad = raw[0] if raw[0] in "xz" else "0"
        raw = pad * (width - len(raw)) + raw
    return raw[-width:] if width else raw


def parse_vcd(path: str | Path) -> Vcd:
    """Minimal VCD reader: scopes, vectors, aliased ids, x/z, $dumpvars blocks."""
    tokens = Path(path).read_text(errors="replace").split()
    scope: list[str] = []
    vars_: dict[str, tuple[str, int, str]] = {}
    widths: dict[str, int] = {}
    changes: dict[str, list[tuple[int, str]]] = {}
    timescale, t, i, n = "", 0, 0, len(tokens)

    def skip_to_end(j):
        while j < n and tokens[j] != "$end":
            j += 1
        return j + 1

    def record(vid, val):
        if vid not in widths:
            return
        lst = changes.setdefault(vid, [])
        val = val if val.startswith("r") else _norm(val, widths[vid])
        if lst and lst[-1][0] == t:
            lst[-1] = (t, val)
        else:
            lst.append((t, val))

    while i < n:
        tok = tokens[i]
        if tok == "$scope":
            scope.append(tokens[i + 2])
            i = skip_to_end(i)
        elif tok == "$upscope":
            scope.pop()
            i = skip_to_end(i)
        elif tok == "$var":
            vtype, width, vid, ref = tokens[i + 1], int(tokens[i + 2]), tokens[i + 3], tokens[i + 4]
            vars_[".".join(scope + [ref])] = (vid, width, vtype)
            widths[vid] = width
            i = skip_to_end(i)
        elif tok == "$timescale":
            j = skip_to_end(i)
            timescale = "".join(tokens[i + 1:j - 1])
            i = j
        elif tok in ("$dumpvars", "$dumpall", "$dumpon", "$dumpoff", "$end"):
            i += 1
        elif tok.startswith("$"):
            i = skip_to_end(i)
        elif tok.startswith("#"):
            t = int(tok[1:])
            i += 1
        elif tok[0] in "bB":
            record(tokens[i + 1], tok[1:])
            i += 2
        elif tok[0] in "rR":
            record(tokens[i + 1], "r" + tok[1:])
            i += 2
        else:
            record(tok[1:], tok[0])
            i += 1
    return Vcd(timescale=timescale, end_time=t, vars=vars_, changes=changes)


# ---------------------------------------------------------------------------
# 2. Cycle sampling
# ---------------------------------------------------------------------------

@dataclass
class Cycles:
    kind: str                       # "clock" or "input-change"
    sample_times: list[int]         # c_k is sampled at sample_times[k]
    strict: list[bool]              # True: value strictly before the time
    boundaries: list[int]           # clock edges / input-change times
    note: str = ""
    tick_offset: int = 0            # input-change mode: boundaries minus probe ticks


def _dut_var(vcd: Vcd, meta: dict, name: str):
    return vcd.vars.get(f'{meta["dut_path"]}.{name}')


def to_cycles(vcd: Vcd, meta: dict) -> Cycles:
    """Clocked: edges e_0..e_{N-1}; c_k = state just before e_k, c_N = end state.
    Combinational: input-change times t_0..; c_k = settled state before t_{k+1}."""
    if meta.get("clock") and _dut_var(vcd, meta, meta["clock"]):
        edge_set: set[int] = set()
        for clk in [meta["clock"]] + meta.get("other_clocks", []):
            var = _dut_var(vcd, meta, clk)
            if not var:
                continue
            prev = "x"   # x→1 at time 0 is a posedge too
            for tm, v in vcd.changes.get(var[0], []):
                if v == "1" and prev in ("0", "x", "z"):
                    edge_set.add(tm)
                prev = v
        edges = sorted(edge_set)
        note = ""
        ticks = meta.get("ticks")
        if ticks is not None and len(edges) != ticks:
            if len(edges) == ticks + 1 and edges[0] == 0:
                edges = edges[1:]
            else:
                note = f"warning: {len(edges)} clock edges in VCD vs {ticks} probe ticks"
        times = edges + [vcd.end_time]
        return Cycles("clock", times, [True] * len(edges) + [False], edges, note)

    change_times = set()
    for name in meta.get("inputs", []) + meta.get("inouts", []):
        var = _dut_var(vcd, meta, name)
        if not var:
            continue
        prev = None
        for tm, v in vcd.changes.get(var[0], []):
            if v != prev:
                change_times.add(tm)
            prev = v
    bounds = sorted(change_times) or [0]
    times = bounds[1:] + [vcd.end_time]
    # The time-0 initial dump is a boundary in the VCD, but the probe only ticks
    # on real input events, so ticks can lag boundaries by one.
    ticks = meta.get("ticks")
    offset, note = 0, ""
    if ticks is not None:
        offset = len(bounds) - ticks
        if offset not in (0, 1):
            note = f"warning: {len(bounds)} input-change times in VCD vs {ticks} probe ticks"
            offset = max(min(offset, 1), 0)
    cyc = Cycles("input-change", times, [True] * (len(bounds) - 1) + [False], bounds, note)
    cyc.tick_offset = offset
    return cyc


def event_cycle(cyc: Cycles, tick: int) -> int:
    """Map a TB line (printed after `tick` probe ticks) to a cycle index."""
    last = len(cyc.sample_times) - 1
    if cyc.kind == "clock":
        return min(tick, last)
    return min(max(tick - 1 + cyc.tick_offset, 0), last)


def fmt_value(val: str, width: int) -> str:
    """Decimal for vectors up to 16 bits, hex above; any x/z bit shows as x."""
    if val.startswith("r"):
        return val[1:]
    if any(c in "xz" for c in val):
        return "z" if set(val) == {"z"} else "x"
    num = int(val, 2) if val else 0
    if width > 16:
        return hex(num)
    return str(num)


def sample(vcd: Vcd, cyc: Cycles, vid: str, width: int) -> list[str]:
    out = []
    for tm, strict in zip(cyc.sample_times, cyc.strict):
        raw = vcd.value_before(vid, tm, width) if strict else vcd.value_at(vid, tm, width)
        out.append(fmt_value(raw, width))
    return out


# ---------------------------------------------------------------------------
# 3. Signal selection
# ---------------------------------------------------------------------------

def select_signals(vcd: Vcd, meta: dict, cone: set[str] | None = None) -> dict:
    """DUT ports plus the internal signals in `cone` (the outputs' dependency
    cone, from VerilogTracer.output_cone; None = keep every DUT signal).
    Returns {"columns": [(display_name, id, width)], "reset": (name, id, width) | None,
             "cone_used": bool}."""
    prefix = meta["dut_path"] + "."

    by_id: dict[str, tuple[str, str, int]] = {}
    for full, (vid, width, vtype) in vcd.vars.items():
        if not full.startswith(prefix) or vtype in ("parameter", "event"):
            continue
        rel = full[len(prefix):]
        best = by_id.get(vid)
        # aliased ids (a port and the net it connects to): keep the shallowest name
        if best is None or (rel.count("."), len(rel)) < (best[0].count("."), len(best[0])):
            by_id[vid] = (rel, vid, width)

    inputs, outputs = meta.get("inputs", []), meta.get("outputs", [])
    inouts = meta.get("inouts", [])
    clocks = {meta.get("clock"), *meta.get("other_clocks", [])}
    reset_name = meta.get("reset")
    reset, ins, ios, mids, outs = None, [], [], [], []
    for rel, vid, width in by_id.values():
        if rel in clocks:
            continue
        if rel == reset_name:
            reset = (rel, vid, width)
            continue
        if rel in inputs:
            ins.append((rel, vid, width))
        elif rel in outputs:
            outs.append((rel, vid, width))
        elif rel in inouts:
            ios.append((rel, vid, width))
        elif cone is None or rel in cone:   # cone names are hierarchical paths below the DUT
            mids.append((rel, vid, width))
    ins.sort(key=lambda c: inputs.index(c[0]))
    outs.sort(key=lambda c: outputs.index(c[0]))
    ios.sort(key=lambda c: inouts.index(c[0]))
    return {"columns": ins + mids + outs + ios, "reset": reset,
            "cone_used": cone is not None}


# ---------------------------------------------------------------------------
# 4. Transition table
# ---------------------------------------------------------------------------

def _periodic(seq: list[str], max_period: int = 8):
    """Smallest period p >= 2 such that a suffix of seq repeats with period p for
    at least 3 full periods. Returns (start_index, p) or None."""
    n = len(seq)
    for p in range(2, max_period + 1):
        if n < 3 * p:
            break
        a = n - p
        while a > 0 and seq[a - 1] == seq[a - 1 + p]:
            a -= 1
        if n - a >= 3 * p and len(set(seq[a:a + p])) > 1:
            return a, p
    return None


def _describe(pattern: list[str]) -> str:
    if len(pattern) == 2 and set(pattern) == {"0", "1"}:
        return "toggles every cycle"
    try:
        nums = [int(v, 0) for v in pattern]
    except ValueError:
        return ""
    lo = nums.index(min(nums))
    rot = nums[lo:] + nums[:lo]
    if all(b - a == 1 for a, b in zip(rot, rot[1:])):
        return f"counts {rot[0]}..{rot[-1]} then wraps"
    hi = nums.index(max(nums))
    rot = nums[hi:] + nums[:hi]
    if all(a - b == 1 for a, b in zip(rot, rot[1:])):
        return f"counts down {rot[0]}..{rot[-1]} then wraps"
    return ""


_UNITS = [("s", 10**15), ("ms", 10**12), ("us", 10**9), ("ns", 10**6), ("ps", 10**3), ("fs", 1)]


def _fmt_time(ticks: int, timescale: str) -> str:
    m = re.match(r"(\d+)\s*(s|ms|us|ns|ps|fs)$", timescale or "")
    if not m:
        return f"{ticks} time units"
    fs = ticks * int(m.group(1)) * dict(_UNITS)[m.group(2)]
    for unit, mult in _UNITS:
        if fs % mult == 0 and fs >= mult:
            return f"{fs // mult}{unit}"
    return f"{fs}fs"


def _tb_text(msgs: list[str], limit: int = 90) -> str:
    out = []
    for m in msgs:
        m = m.replace("|", "/")
        out.append(m if len(m) <= limit else m[:limit - 1] + "…")
    return "; ".join(out)


def _change_line(label: str, changes: list[str], tb: str) -> str:
    """One row: 'c107: a 1→2, b 0→1 | TB: [FAIL] ...' (only the signals that changed)."""
    line = f"{label}: {', '.join(changes)}" if changes else f"{label}:"
    return f"{line} | TB: {tb}" if tb else line


def _cap_rows(body: list[tuple[int, str]], anchor: int, max_rows: int):
    """Keep the max_rows rows nearest the anchor cycle; mark each gap."""
    if len(body) <= max_rows:
        return body, 0
    keep = sorted(sorted(range(len(body)), key=lambda i: (abs(body[i][0] - anchor), i))[:max_rows])
    out, prev, omitted = [], -1, 0

    def gap_row(gap):
        return (gap[0][0], f"… {len(gap)} rows omitted (c{gap[0][0]}{DASH}c{gap[-1][0]}) …")

    for i in keep:
        if i != prev + 1:
            gap = body[prev + 1:i]
            omitted += len(gap)
            out.append(gap_row(gap))
        out.append(body[i])
        prev = i
    if prev != len(body) - 1:
        gap = body[prev + 1:]
        omitted += len(gap)
        out.append(gap_row(gap))
    return out, omitted


def build_waveform(vcd_path: str, meta: dict, test_details: list[dict],
                   cone: set[str] | None = None, max_rows: int = 60, after_fail: int = 0,
                   min_periodic_window: int = 16) -> dict:
    """Build the transition table.

    Returns {"text", "dense_values", "stats"}: "text" is what goes in the prompt;
    "dense_values" (signal -> cycle -> value) is for checking with reconstruct_dense.
    """
    vcd = parse_vcd(vcd_path)
    cyc = to_cycles(vcd, meta)
    sel = select_signals(vcd, meta, cone)
    ncyc = len(cyc.sample_times)
    vals = {name: sample(vcd, cyc, vid, w) for name, vid, w in sel["columns"]}

    # TB events per cycle
    events: dict[int, list[str]] = {}
    first_fail = None
    for td in test_details:
        if "tick" not in td:
            continue
        c = event_cycle(cyc, td["tick"])
        tag = "[FAIL]" if td.get("status") == "fail" else "[PASS]"
        events.setdefault(c, []).append(f"{tag} {td.get('message', '')}")
        if td.get("status") == "fail" and first_fail is None:
            first_fail = c
    n_fail = sum(td.get("status") == "fail" for td in test_details)

    # Reset and window
    header = []
    unit = "edge" if cyc.kind == "clock" else "input change"
    if cyc.kind == "clock":
        period = ""
        if len(cyc.boundaries) > 1:
            diffs = sorted(b - a for a, b in zip(cyc.boundaries, cyc.boundaries[1:]))
            period = f", period {_fmt_time(diffs[len(diffs) // 2], vcd.timescale)}"
        clks = [meta["clock"]] + meta.get("other_clocks", [])
        if len(clks) > 1:
            edge_txt = f"posedge of any of {', '.join(clks)} (merged)"
            period = ""   # the spacing between merged edges is not a clock period
        else:
            edge_txt = f"posedge of {meta['clock']}"
        header.append(f"[Waveform] DUT {meta['dut_path']} ({meta['dut_module']}). "
                      f"cN = values just before the N-th {edge_txt}{period}; "
                      f"c{ncyc - 1} = end of simulation.")
    else:
        header.append(f"[Waveform] DUT {meta['dut_path']} ({meta['dut_module']}), no clock. "
                      f"cN = settled values after the N-th change of the DUT inputs.")
    if cyc.note:
        header.append(cyc.note)

    ws = 0
    if sel["reset"]:
        rname, rvid, rw = sel["reset"]
        rv = sample(vcd, cyc, rvid, rw)
        active = "0" if meta.get("reset_active_low") else "1"
        was_active = [k for k, v in enumerate(rv) if v == active]
        released = [k for k, v in enumerate(rv)
                    if v not in (active, "x", "z") and was_active and k > was_active[0]]
        if not was_active:
            header.append(f"reset: {rname} never asserted (value at c0: {rv[0]})")
        elif not released:
            header.append(f"reset: {rname} asserted from c{was_active[0]} and never released")
        else:
            ws = released[0]
            reasserted = [k for k in was_active if k > ws]
            extra = f", re-asserted at c{reasserted[0]}" if reasserted else ""
            header.append(f"reset: {rname} (active-{'low' if active == '0' else 'high'}) "
                          f"released at c{ws}{extra}")

    # window around the first failure: from the last [PASS] before it (else from the
    # start), up to the failure itself
    last_pass = None
    if first_fail is not None:
        passes = [c for c, msgs in events.items() if c < first_fail
                  and any(m.startswith("[PASS]") for m in msgs)]
        if passes and max(passes) > ws:
            last_pass = max(passes)
            ws = last_pass
    we = ncyc - 1 if first_fail is None else min(first_fail + after_fail, ncyc - 1)
    checks = len([td for td in test_details if "tick" in td])
    if first_fail is not None:
        start_txt = (f"from the last [PASS] at c{last_pass}" if last_pass is not None
                     else "no [PASS] before it, so from the start")
        header.append(f"window: c{ws}{DASH}c{we} ({start_txt} to the first [FAIL] at c{first_fail}; "
                      f"{n_fail} of {checks} TB checks failed)")
    else:
        header.append(f"window: c{ws}{DASH}c{we} (no [FAIL] printed; TB printed {checks} check lines)")
    cone_txt = ("dependency cone of the DUT outputs" if sel["cone_used"]
                else "all DUT signals (dependency tracer unavailable)")
    clk_names = [meta["clock"]] + meta.get("other_clocks", []) if meta.get("clock") else []
    clk_txt = f"; {', '.join(clk_names)} omitted" if clk_names else ""
    header.append(f"signals: {cone_txt}, plus DUT inputs{clk_txt}")

    names = [c[0] for c in sel["columns"]]
    win = range(ws, we + 1)
    constant = [n for n in names if len({vals[n][k] for k in win}) == 1]

    # columns with identical values in the window: keep one (a port if possible)
    ports = (set(meta.get("inputs", [])) | set(meta.get("outputs", []))
             | set(meta.get("inouts", [])))
    # internal signals identical to another column (and not trivially so): fold them
    width_of = {name: w for name, _, w in sel["columns"]}
    groups: dict[tuple, list[str]] = {}
    for n in names:
        seq = [vals[n][k] for k in win]
        if n not in constant and sum(a != b for a, b in zip(seq, seq[1:])) >= 2:
            groups.setdefault((width_of[n],) + tuple(seq), []).append(n)
    same_as: dict[str, list[str]] = {}
    for members in groups.values():
        internal = [m for m in members if m not in ports]
        if len(members) > 1 and internal:
            keep = next((m for m in members if m in ports), members[0])
            folded = [m for m in internal if m != keep]
            if folded:
                same_as[keep] = folded
    hidden = {m for v in same_as.values() for m in v}

    periodic = {}
    for n in names:
        if n in constant or n in hidden or len(win) < min_periodic_window:
            continue
        hit = _periodic([vals[n][k] for k in win])
        if hit:
            a, p = hit
            periodic[n] = (ws + a, p)
    if constant:
        label = ("never change after reset" if sel["reset"] and ws and last_pass is None
                 else "never change in window")
        header.append(f"{label}: " + ", ".join(f"{n}={vals[n][ws]}" for n in constant))
    for n, (start, p) in periodic.items():
        pat = [vals[n][start + j] for j in range(p)]
        desc = _describe(pat)
        header.append(f"periodic: {n} from c{start} repeats [{', '.join(pat)}] period {p}"
                      f"{' (' + desc + ')' if desc else ''} through c{we}")

    for keep, others in same_as.items():
        header.append(f"same values as {keep}: {', '.join(others)}")

    cols = [n for n in names if n not in constant and n not in hidden]
    show_until = {n: periodic[n][0] + periodic[n][1] - 1 for n in periodic}

    # rows: only cycles where a signal changed or the TB printed something; each
    # lists only the signals that changed 
    body: list[tuple[int, str]] = []   # (cycle, line)
    base = [_change_line(f"c{ws} start", [], _tb_text(events[ws]))] if events.get(ws) else []

    for k in range(ws + 1, we + 1):
        changes = [f"{n} {vals[n][k - 1]}{ARROW}{vals[n][k]}" for n in cols
                   if vals[n][k - 1] != vals[n][k] and not (n in show_until and k > show_until[n])]
        tb = _tb_text(events.get(k, []))
        if changes or tb:
            body.append((k, _change_line(f"c{k}", changes, tb)))

    # budget: keep rows nearest the anchor (first failure, else window start)
    anchor = first_fail if first_fail is not None else ws
    body, omitted = _cap_rows(body, anchor, max_rows)

    later = [c for c in events if c > we]
    if later:
        header.append(f"(TB lines after c{we} not shown: {sum(len(events[c]) for c in later)})")

    header.append(f"changes per cycle (signal old{ARROW}new; cycles and signals with no change not listed):")
    text = "\n".join(header + base + [line for _, line in body])

    return {
        "text": text,
        "dense_values": {n: {k: vals[n][k] for k in win} for n in names},
        "stats": {
            "cycles_total": ncyc, "window": [ws, we], "first_fail": first_fail,
            "columns": len(cols), "signals": len(names), "constant": len(constant),
            "periodic": len(periodic), "rows": len(body), "rows_omitted": omitted,
            "chars": len(text),
        },
    }


# ---------------------------------------------------------------------------
# 5. Entry point for the debug agent
# ---------------------------------------------------------------------------

def waveform_context(tb_result: dict, cone: set[str] | None = None,
                     max_rows: int = 60) -> tuple[str, dict]:
    """Transition-table prompt text for one testbench run, plus info for the report.

    tb_result is the dict from tools/benchmark_runner.py run with --waveform-dir.
    cone is VerilogTracer.output_cone() of the simulated design.
    Returns ("", info) when there is no waveform (compile failure, no VCD) —
    info["error"] says why.
    """
    info: dict = {}
    if tb_result.get("all_pass"):
        info["skipped"] = "all testbench checks passed"
        return "", info
    if not tb_result.get("vcd_file") or not tb_result.get("wave_meta"):
        info["error"] = (tb_result.get("wave_error")
                         or ("design did not compile" if not tb_result.get("compiled")
                             else "no waveform recorded"))
        return "", info
    try:
        w = build_waveform(tb_result["vcd_file"], tb_result["wave_meta"],
                           tb_result.get("test_details", []), cone=cone, max_rows=max_rows)
    except Exception as e:  # never let the waveform break a debug run
        info["error"] = f"{type(e).__name__}: {e}"
        return "", info
    text = w["text"]
    info["stats"] = w["stats"]
    info["chars"] = len(text)
    return text, info


# ---------------------------------------------------------------------------
# 6. Round-trip check
# ---------------------------------------------------------------------------

def reconstruct_dense(text: str) -> dict[str, dict[int, str]]:
    """Rebuild per-cycle values from the transition-table text alone.
    Raises ValueError if the text is inconsistent (e.g. an old value that does
    not match the current one). Rows marked as omitted cannot be rebuilt."""
    lines = text.splitlines()
    m = next(re.search(rf"window: c(\d+){DASH}c(\d+)", l) for l in lines if l.startswith("window:"))
    ws, we = int(m.group(1)), int(m.group(2))
    if any("rows omitted" in l for l in lines):
        raise ValueError("table was truncated; rebuild with max_rows large enough")

    out: dict[str, dict[int, str]] = {}
    for l in lines:
        if l.startswith("never change"):
            for item in l.split(": ", 1)[1].split(", "):
                n, v = item.split("=", 1)
                out[n] = {k: v for k in range(ws, we + 1)}
    periodic = {}
    for l in lines:
        pm = re.match(r"periodic: (\S+) from c(\d+) repeats \[(.*?)\] period (\d+)", l)
        if pm:
            periodic[pm.group(1)] = (int(pm.group(2)), pm.group(3).split(", "))

    # change rows; a signal's value before its first change is that change's "old" side
    cur: dict[str, str] = {}
    changes_at: dict[int, list[tuple[str, str, str]]] = {}
    for l in lines:
        m = re.match(r"c(\d+)(?: start)?:(.*)$", l)
        if not m:
            continue
        k = int(m.group(1))
        changes = m.group(2).split(" | TB: ", 1)[0].strip()
        for item in (changes.split(", ") if changes else []):
            n, change = item.split(" ", 1)
            old, new = change.split(ARROW)
            cur.setdefault(n, old)
            changes_at.setdefault(k, []).append((n, old, new))
    for k in range(ws, we + 1):
        for n, old, new in changes_at.get(k, []):
            if old != cur[n]:
                raise ValueError(f"{n} at c{k}: table says {old}{ARROW}{new} but value was {cur[n]}")
            cur[n] = new
        for n, v in cur.items():
            out.setdefault(n, {})[k] = v
    for n, (start, pat) in periodic.items():
        for k in range(start, we + 1):
            out.setdefault(n, {})[k] = pat[(k - start) % len(pat)]
    for l in lines:
        sm = re.match(r"same values as (\S+): (.*)$", l)
        if sm:
            for alias in sm.group(2).split(", "):
                out[alias] = dict(out[sm.group(1)])
    return out
