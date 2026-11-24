"""
Predict-and-force localization (the logic of predict_and_force.ipynb, for the agent loop).

After a failed iteration, a smaller model predicts what the signals on the failing output's dependency path
should be for a few cycles before the first failing testbench check. The prediction is forced into the simulation and
if the check still fails, the model gets feedback and tries again (up to `rounds` times). From the accepted prediction,
only the changes the check needs are kept, then the dependency graph is walked back to the first signal that is wrong
while its inputs are right. Its driving lines are the suspicious lines handed to the main model in the refinement prompt.

    from agent.predict_force import localize
    result = localize("fixed_iter1.v", "tb.v", design_description)
    result["prompt_block"]   # text for the refinement prompt ("" if nothing was found)

Settings: the `localizer:` section of config.yaml (server keys default to the `vllm:` section).
"""

from __future__ import annotations

import collections
import contextlib
import io
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from itertools import count, permutations, zip_longest

import requests
from pyverilog.vparser import ast as vast

from agent.backends.config import load_config_section
from agent.verilog_tracer import VerilogTracer
from agent.waveform import parse_vcd, to_cycles, event_cycle, sample, build_waveform
from tools.benchmark_runner import run_benchmark

logger = logging.getLogger(__name__)

DEFAULTS = dict(model="Qwen/Qwen3-8B", port=8001, gpu_ids="2", gpu_memory_utilization=0.6,
                max_model_len=16384, rounds=4, window=4, max_signals=10, temperature=0.7, top_p=0.95,
                top_k=20, max_new_tokens=1024, think=False, use_deps=True,
                force=True)   # force=False: ablation, one unchecked prediction (no forced simulations)
RADIX = {"h": 16, "b": 2, "d": 10}
VALUE = r"(?<![\w.'])(?:(\d+)?'([hHbBdD]))?([0-9a-fA-FxXzZ_]+)\b"   # 179, 16'h2d61, 8'b1010, xx


# ---- Settings and server ----

def load_settings() -> dict:
    s = {**DEFAULTS, **(load_config_section("localizer") or {})}
    if "seed" not in s:   # base sampling seed: localizer's own, else the main vllm: seed (null = unseeded)
        s["seed"] = (load_config_section("vllm") or {}).get("seed")
    return s


def ensure_server(s: dict | None = None) -> str:
    """Base URL of the localizer's vLLM server, started (same vLLM settings as the main one) if needed."""
    from agent.backends import vllm_server
    s = s or load_settings()
    srv = {**vllm_server.load_settings(), **{k: s[k] for k in
           ("port", "gpu_ids", "gpu_memory_utilization", "max_model_len") if k in s}}
    srv["tensor_parallel_size"] = s.get("tensor_parallel_size", 1)
    return vllm_server.start_server(s["model"], srv)[0]


# ---- Simulation ----

def dec(v: str) -> str:
    """waveform.py prints vectors wider than 16 bits as hex; use decimal everywhere."""
    return str(int(v, 16)) if v.startswith("0x") else v


def simulate(design: str, tb: str, extra=()):
    """Run the testbench (plus optional force file); sample every DUT signal per cycle."""
    r = run_benchmark([design, *extra], tb, source=os.path.dirname(design), waveform_dir=tempfile.mkdtemp())
    if not r.vcd_file:
        return r, None
    vcd = parse_vcd(r.vcd_file)
    cyc = to_cycles(vcd, r.wave_meta)
    pre = r.wave_meta["dut_path"] + "."
    waves = {n[len(pre):]: [dec(v) for v in sample(vcd, cyc, vid, w)] for n, (vid, w, t) in vcd.vars.items()
             if n.startswith(pre) and t not in ("parameter", "event")}
    widths = {n[len(pre):]: w for n, (vid, w, t) in vcd.vars.items() if n.startswith(pre)}
    return r, dict(vcd=vcd, cyc=cyc, waves=waves, widths=widths, timescale=vcd.timescale)


@dataclass
class Design:
    """The failing run of one design and the cells (signals x cycles) the model predicts."""
    path: str
    tb: str
    r: object                      # BenchmarkResult of the unforced run
    sim: dict                      # simulate()'s waves/widths/cycles
    tracer: VerilogTracer
    fail: int                      # index of the first failing check
    fail_cycle: int
    signals: list = field(default_factory=list)
    cycles: list = field(default_factory=list)
    last_pass: int | None = None   # cycle of the last passing check before the failure
    layouts: list = field(default_factory=list)
    port_width: dict = field(default_factory=dict)
    sims: int = 0                  # forced simulations run

    def __post_init__(self):
        self.meta, self.waves, self.G = self.r.wave_meta, self.sim["waves"], self.tracer.graph
        self.outs = [o for o in self.meta["outputs"] if o in self.waves]
        self.ports = set(self.meta["inputs"]) | set(self.meta["outputs"])
        self.clocked = self.sim["cyc"].kind == "clock"

    def width(self, s): return self.sim["widths"].get(s, 1)
    def cycle_of(self, check): return event_cycle(self.sim["cyc"], check["tick"])
    def is_reg(self, s): return any(d.get("node_type") == "seq" for _, _, d in self.G.in_edges(s, data=True))
    def preds(self, s): return sorted(self.G.predecessors(s)) if s in self.G else []


def load_design(bug_path, tb_path, tracer=None) -> Design | str:
    """Simulate the design and find its first failing check; a string says why there is nothing to localize."""
    r, sim = simulate(bug_path, tb_path)
    if sim is None or not r.wave_meta:
        return "no waveform (compile error?)"
    fi = next((i for i, d in enumerate(r.test_details) if d["status"] == "fail"), None)
    if fi is None:
        return "no failing check"
    fc = event_cycle(sim["cyc"], r.test_details[fi]["tick"])
    if r.test_details[fi]["message"].strip().upper() == "TIMEOUT" or fc >= len(next(iter(sim["waves"].values()))):
        return "failure is a timeout"
    if tracer is None:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            tracer = VerilogTracer(bug_path)
    return Design(bug_path, tb_path, r, sim, tracer, fi, fc)


def choose_cells(d: Design, targets, max_signals, window):
    """Signals: BFS back from the failing outputs, one driver per parent in turn (so a wide fan-in can't fill
    the budget). Cycles: the last `window` up to the failure, plus the last passing check as an anchor."""
    seen, order, frontier = set(targets), list(targets), list(targets)
    while frontier and len(order) < max_signals:
        groups = [[p for p in d.preds(n) if p not in seen and p not in d.meta["inputs"] and p in d.waves]
                  for n in frontier]
        frontier = []
        for p in (p for row in zip_longest(*groups) for p in row if p):
            if p not in seen:
                seen.add(p); order.append(p); frontier.append(p)
    d.signals = order[:max_signals]
    d.cycles = list(range(max(0, d.fail_cycle - window + 1), d.fail_cycle + 1))
    passes = [d.cycle_of(x) for x in d.r.test_details[:d.fail] if x["status"] == "pass"]
    d.last_pass = max((c for c in passes if c < d.fail_cycle), default=None)
    if d.last_pass is not None and d.last_pass not in d.cycles:
        d.cycles.insert(0, d.last_pass)


# ---- What the testbench says: named values, unnamed values, port widths ----

def tb_values(message: str) -> dict:
    """{name: int} for `name=value` pairs (decimal, or N'h / N'b / N'd)."""
    out = {}
    for name, base, digits in re.findall(r"\b(\w+)\s*=\s*(?:\d*'([hHbBdD]))?([0-9a-fA-F]+)\b", message):
        try:
            out[name] = int(digits, RADIX.get(base.lower(), 10))
        except ValueError:
            pass
    return out


def split_msg(msg: str) -> tuple[str, str]:
    """(expected part, got part) of a check message; either may be empty."""
    got = re.search(r"\b(?:got|actual|observed|but)\b", msg, re.I)
    exp = re.search(r"\bexpect\w*", msg[:got.start()] if got else msg, re.I)
    return (msg[exp.end():got.start() if got else None] if exp else "", msg[got.end():] if got else "")


def unnamed(part: str, outs) -> list[tuple]:
    """Value tokens not written as `output=value`, e.g. 179 in `expected 179`."""
    part = re.sub(rf"\b(?:{'|'.join(map(re.escape, outs))})\s*=\s*\S+", " ", part) if outs else part
    return [t for t in re.findall(VALUE, part) if t[1] or re.search(r"\d", t[2]) or re.fullmatch(r"[xXzZ]+", t[2])]


def to_int(tok, radix):
    _, base, digits = tok
    try:
        return int(digits.replace("_", ""), RADIX[base.lower()] if base else radix)
    except ValueError:
        return None


def fits(tok, radix, bits) -> bool:
    """A well-formed number of `bits` bits in this radix (hex/binary digit count must match)."""
    width, base, digits = tok
    v, per_digit = to_int(tok, radix), {16: 4, 2: 1}.get(radix)
    return (v is not None and v < 2 ** bits and (not width or int(width) == bits)
            and bool(base or not per_digit or len(digits.replace("_", "")) == -(-bits // per_digit)))


def learn_layouts(d: Design) -> list:
    """For the k-th unnamed value: (outputs it concatenates, radix), the only reading that matches the simulated
    outputs wherever the log prints them and parses in every message. Ambiguous -> None."""
    outs, n = d.outs, len(d.waves[d.outs[0]]) if d.outs else 0
    obs = []                                      # (k, token, simulated outputs or None = format only)
    for x in d.r.test_details:
        c = d.cycle_of(x)
        act = {o: d.waves[o][c] for o in outs} if c < n else None
        exp, got = (unnamed(p, outs) for p in split_msg(x["message"]))
        obs += [(k, t, act) for k, t in enumerate(got)]
        obs += [(k, t, act if x["status"] == "pass" else None) for k, t in enumerate(exp)]
    obs = [o for o in obs if not re.search(r"[xXzZ]", o[1][2])]

    def support(k, lay, radix):
        """Nonzero matches of this reading, or None if any message contradicts it."""
        bits, hits = sum(d.width(o) for o in lay), 0
        for kk, tok, act in obs:
            if kk != k:
                continue
            if not fits(tok, radix, bits):
                return None
            if act and all(act[o].isdigit() for o in lay):
                want = 0
                for o in lay:
                    want = (want << d.width(o)) | int(act[o])
                if to_int(tok, radix) != want:
                    return None
                hits += want != 0                 # zeros fit almost any reading
        return hits

    layouts = []
    for k in range(max((o[0] + 1 for o in obs), default=0)):
        found = [(s, lay, radix) for lay in (l for m in range(1, min(3, len(outs)) + 1) for l in permutations(outs, m))
                 for radix in (16, 2, 10) if (s := support(k, lay, radix)) is not None]
        top = [f for f in found if f[0] == max((f[0] for f in found), default=0)]
        layouts.append(top[0][1:] if len({f[1] for f in top}) == 1 else None)
    return layouts


def decode(d: Design, msg: str) -> tuple[dict, dict]:
    """({output: expected}, {output: got}) from one check message."""
    out = []
    for part in split_msg(msg):
        vals = {o: v for o, v in tb_values(part).items() if o in d.outs}
        for tok, lay in zip(unnamed(part, d.outs), d.layouts):
            v = to_int(tok, lay[1]) if lay else None
            for o in reversed(lay[0] if v is not None else ()):
                vals.setdefault(o, v & ((1 << d.width(o)) - 1))
                v >>= d.width(o)
        out.append(vals)
    return out[0], out[1]


def port_widths(d: Design) -> dict:
    """Port widths as the testbench declares them: its net of the same name, else the one with the same values."""
    vcd, top = d.sim["vcd"], d.meta["tb_top"] + "."
    nets = {n[len(top):]: (vid, w) for n, (vid, w, t) in vcd.vars.items() if n.startswith(top) and "." not in n[len(top):]}
    out = {}
    for p in (p for p in d.meta["inputs"] + d.meta["outputs"] if p in d.waves):
        same = [nets[p]] if p in nets else [(vid, w) for vid, w in nets.values()
                                            if [dec(v) for v in sample(vcd, d.sim["cyc"], vid, w)] == d.waves[p]]
        if len(same) == 1:
            out[p] = same[0][1]
    return out


# ---- Known cells: testbench values, the last passing check, and plain wiring ----

def known_cells(d: Design) -> dict:
    """{signal: {"c<k>": value}} the testbench tells us, plus simulated values at the last passing check."""
    known = {}
    for x in d.r.test_details[:d.fail + 1]:
        c = d.cycle_of(x)
        exp, got = decode(d, x["message"])
        vals = ({**tb_values(x["message"]), **exp, **got} if x["status"] == "pass"
                else exp or tb_values(re.split(r"\bgot\b", x["message"])[0]))
        for s, v in vals.items():
            if s in d.signals and c in d.cycles:
                known.setdefault(s, {})[f"c{c}"] = str(v)
    if d.last_pass is not None:
        dense = build_waveform(d.r.vcd_file, d.meta, d.r.test_details, cone=set(d.signals))["dense_values"]
        for s in d.signals:
            v = dec(dense.get(s, {}).get(d.last_pass, d.waves[s][d.last_pass]))
            if v.isdigit():
                known.setdefault(s, {}).setdefault(f"c{d.last_pass}", v)
    return known


def port_slices(tracer: VerilogTracer) -> dict:
    """Instance ports wired to a bit or slice of a parent signal: {'FA1.sum': ('sum', 1)} from `.sum(sum[1])`."""
    out = {}
    def lsb(e):
        try: return int(str(e.value).split("'")[-1].lstrip("dDhHbB"), 10)
        except (AttributeError, ValueError): return None
    def walk(n):
        if isinstance(n, vast.Instance):
            for pa in n.portlist or []:
                a, key = pa.argname, f"{n.name}.{pa.portname}"
                if pa.portname is None or a is None: continue
                if isinstance(a, vast.Identifier): out[key] = (a.name, 0)
                elif isinstance(a, vast.Pointer) and lsb(a.ptr) is not None: out[key] = (a.var.name, lsb(a.ptr))
                elif isinstance(a, vast.Partselect) and lsb(a.lsb) is not None: out[key] = (a.var.name, lsb(a.lsb))
        for ch in n.children(): walk(ch)
    walk(tracer.ast)
    return out


def propagate_wiring(d: Design, known: dict) -> dict:
    """Fill cells that follow from known ones through plain wiring (`assign clock = cnt`, `red <= p_red`, a bus of
    instance outputs). Y is wiring into X when it is a direct driver and, over the whole simulation, Y (a cycle
    earlier if registered) always equals one bit slice of X; a port connection gives the slice directly. A value
    that does not fit Y while Y is X's only driver is not propagated: that wire may be the bug."""
    W, n = d.waves, len(d.waves[d.signals[0]])
    rst = d.meta.get("reset") if d.meta.get("reset") in W else None
    same_reset = lambda *cs: not rst or all(W[rst][c] == W[rst][d.fail_cycle] for c in cs)
    mask = lambda s: (1 << d.width(s)) - 1
    ports, rel = port_slices(d.tracer), {}
    for x in d.signals:
        for y in (p for p in d.preds(x) if p in d.signals and p != x):
            lag = 1 if (d.G.get_edge_data(y, x) or {}).get("node_type") == "seq" else 0
            cyc = [c for c in range(lag, n) if W[x][c].isdigit() and W[y][c - lag].isdigit() and same_reset(c, c - lag)]
            holds = lambda lo: all((int(W[x][c]) >> lo) & mask(y) == int(W[y][c - lag]) for c in cyc)
            wired = [lo for k, (var, lo) in ports.items() if y.endswith(k) and y[:-len(k)] + var == x]
            if not wired and len({W[y][c - lag] for c in cyc}) < 2:
                continue                          # y never changes: no evidence either way
            los = [lo for lo in (wired or range(d.width(x) - d.width(y) + 1)) if holds(lo)]
            if len(los) == 1:
                rel[(x, y)] = (lag, los[0])
    changed = True
    while changed:
        changed = False
        for (x, y), (lag, lo) in rel.items():
            only_driver = not [p for p in d.preds(x) if p not in (x, y)]
            for c in d.cycles:
                v, k = known.get(x, {}).get(f"c{c}"), f"c{c - lag}"
                if (v is None or c - lag not in d.cycles or k in known.get(y, {}) or not same_reset(c, c - lag)
                        or (only_driver and int(v) & ~(mask(y) << lo))):
                    continue
                known.setdefault(y, {})[k] = str((int(v) >> lo) & mask(y))
                changed = True
    return rel


# ---- Prompt and model ----

def stimulus_table(d: Design, max_rows=40) -> str:
    """Inputs up to the failure, one row per change. Regularly toggling 1-bit inputs (a second clock) are
    listed once with their period instead of a row per toggle."""
    W = d.waves
    ins = [i for i in d.meta["inputs"] if i != d.meta.get("clock") and i in W]
    clocks = {}
    for i in ins:
        ch = [c for c in range(1, len(W[i])) if W[i][c] != W[i][c - 1]]
        gaps = [b - a for a, b in zip(ch, ch[1:])]
        if d.width(i) == 1 and len(ch) >= len(W[i]) / 4 and len(set(gaps[1:])) <= 2:
            clocks[i] = sum(gaps[1:3])            # one period = a high gap + a low gap
    ins = [i for i in ins if i not in clocks]
    rows, prev = [], None
    for c in range(d.fail_cycle + 1):
        cur = tuple(W[i][c] for i in ins)
        if cur != prev or c == d.fail_cycle:
            rows.append(f"  c{c}: " + ", ".join(f"{i}={v}" for i, v in zip(ins, cur)))
        prev = cur
    if len(rows) > max_rows:
        rows = rows[:5] + [f"  ... ({len(rows) - max_rows} rows omitted)"] + rows[-(max_rows - 5):]
    head = [f"  {i}: clock, period {p} cycles (not listed below)" for i, p in clocks.items()]
    return "\n".join(head + rows) + "\n  (rows only where an input changed; values hold until the next row)"


def number(text: str) -> str:
    return "\n".join(f"{i + 1:3d}| {l}" for i, l in enumerate(text.splitlines()))


def build_prompt(d: Design, description: str, deps: str, known: dict) -> str:
    skeleton = {s: {f"c{c}": known.get(s, {}).get(f"c{c}", "?") for c in d.cycles} for s in d.signals}
    msgs = "\n".join(f"  c{d.cycle_of(x)}: [{x['status'].upper()}] {x['message']}" for x in d.r.test_details[:d.fail + 1])
    widths = ", ".join(f"{s} {d.port_width[s]} bit" for s in d.signals if s in d.port_width)
    gaps = d.cycles != list(range(d.cycles[0], d.cycles[-1] + 1))
    return f"""You are checking a Verilog design that has a bug. Work out what the internal signals SHOULD be
if the design matched its description. Do not just execute the code: the code may be wrong.

## Description
{description or "(none)"}

## Code (may contain a bug)
{number(open(d.path).read())}

{"## Dependencies (from the code)" + chr(10) + deps + chr(10) + chr(10) if deps else ""}## Cycles
{"c<k> = value just before the k-th rising clock edge (registers show the value set at the previous edge)."
 if d.clocked else "c<k> = settled value after the k-th change of the inputs."}
{"Cycles " + ", ".join(f"c{c}" for c in d.cycles) + " (cycles not listed are skipped)." if gaps else ""}

## Inputs applied
{stimulus_table(d)}

## Testbench messages up to the first failure
{msgs}

## Task
Fill in the CORRECT value of every "?" below. Use decimal numbers.
{"Port widths (from the testbench): " + widths + "." + chr(10) if widths else ""}Cells that already have a number are known and correct: they come from the testbench checks, from the
simulation at the last check that passed, or follow from them through plain wiring. Your other values must agree
with them (e.g. a register feeding an output must hold the value the testbench expects).
Write "unknown" only if the description really does not determine it.
Answer with JSON only, in exactly this shape:
{json.dumps(skeleton, indent=1)}
"""


def parse_prediction(d: Design, text: str, known: dict) -> dict:
    """First JSON object in the answer -> {signal: {"c<k>": decimal | "unknown"}}; known cells win."""
    text = text.rpartition("</think>")[2]
    i = text.find("{")
    try:
        obj = json.JSONDecoder().raw_decode(text[i:])[0] if i >= 0 else {}
    except json.JSONDecodeError:
        obj = {}
    pred = {}
    for s in d.signals:
        row = obj.get(s) if isinstance(obj.get(s), dict) else {}
        pred[s] = {}
        for c in d.cycles:
            v = str(row.get(f"c{c}", "unknown")).strip().lower()
            v = str(int(v, 16)) if re.fullmatch(r"0x[0-9a-f]+", v) else v if v.isdigit() else "unknown"
            if v != "unknown" and int(v) >= 2 ** d.port_width.get(s, 10 ** 6):
                v = "unknown"
            pred[s][f"c{c}"] = known.get(s, {}).get(f"c{c}", v)
    return pred


_REQUEST_NO = count()   # localizer requests sent by this process (see ask())
USAGE = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}   # running totals (see localize())


def ask(url: str, S: dict, messages: list) -> str:
    body = dict(model=S["model"], messages=messages, max_tokens=S["max_new_tokens"], temperature=S["temperature"],
                top_p=S["top_p"], top_k=S["top_k"], chat_template_kwargs={"enable_thinking": bool(S["think"])})
    if S.get("seed") is not None:
        # seed + request number, the same rule as the main client and the baselines.
        body["seed"] = S["seed"] + next(_REQUEST_NO)
    r = requests.post(f"{url}/v1/chat/completions", json=body, timeout=600)
    r.raise_for_status()
    resp = r.json()
    usage = resp.get("usage") or {}
    USAGE["llm_calls"] += 1
    USAGE["prompt_tokens"] += usage.get("prompt_tokens", 0)
    USAGE["completion_tokens"] += usage.get("completion_tokens", 0)
    return resp["choices"][0]["message"].get("content") or ""


# ---- Forcing ----

def to_force(d: Design, pred: dict, signals=None) -> dict:
    """{signal: {cycle: int}} for the predicted numbers of non-port signals."""
    return {s: {int(c[1:]): int(v) for c, v in pred[s].items() if v.isdigit()}
            for s in (signals or d.signals) if s not in d.ports and s in pred}


def force_file(d: Design, values: dict) -> str:
    """Verilog module that forces each value in its cycle's time slot (released at the end of each run)."""
    b, st, dut = d.sim["cyc"].boundaries, d.sim["cyc"].sample_times, d.meta["dut_path"]
    start = (lambda k: 0 if k == 0 else b[k - 1] + 1) if d.clocked else (lambda k: b[min(k, len(b) - 1)])
    events = collections.defaultdict(list)
    for s, per in values.items():
        for k in sorted(per):
            events[start(k)].append(f"force {dut}.{s} = {d.width(s)}'d{per[k]};")
            if k + 1 not in per:
                events[st[k] + 1].append(f"release {dut}.{s};")
    lines, now = [], 0
    for t in sorted(events):
        lines.append(f"    #{t - now}; " + " ".join(events[t])); now = t
    ts = d.sim["timescale"].replace(" ", "")
    return f"`timescale {ts}/{ts}\nmodule __force;\n  initial begin\n" + "\n".join(lines) + "\n  end\nendmodule\n"


def run_forced(d: Design, values: dict) -> dict:
    """Simulate with `values` forced; fixed = the failing check passes and no earlier check breaks."""
    with tempfile.NamedTemporaryFile("w", suffix="_force.v", delete=False) as f:
        f.write(force_file(d, {s: v for s, v in values.items() if v}))
    try:
        r, sim = simulate(d.path, d.tb, extra=[f.name])
    finally:
        os.remove(f.name)
    d.sims += 1
    t, fi = r.test_details, d.fail
    broke = [x["message"] for x in t[:fi] if x["status"] == "fail"]
    return dict(fixed=len(t) > fi and t[fi]["status"] == "pass" and not broke, sim=sim, broke=broke,
                target=t[fi]["message"] if len(t) > fi else "(check not reached)")


def feedback(d: Design, pred: dict, forced: dict) -> str:
    """Which outputs are still wrong and which of the model's own forced values drive them."""
    exp, got = decode(d, forced["target"])
    forced_sigs, c, lines = set(to_force(d, pred)), d.fail_cycle, []
    for o in (o for o in exp if o in got and exp[o] != got[o]):
        reg = o in d.G and d.is_reg(o)
        cin = c - 1 if reg else c
        drivers = [f"{p} = {pred[p][f'c{cin}']} at c{cin}" for p in d.preds(o) if p in forced_sigs and cin in d.cycles]
        lines.append(f"- {o} is {got[o]} at c{c} but the testbench expects {exp[o]}."
                     + (f" {o} {'is a register fed' if reg else 'is fed'} by your values: {', '.join(drivers)}." if drivers else ""))
    lines += [f"- this earlier check now fails: {b}" for b in forced["broke"]]
    return ("I forced your predicted values into the simulation. The failing check still fails:\n  "
            + forced["target"] + "\n" + "\n".join(lines)
            + "\nSome of your values must be wrong. Fix them so every output matches the testbench. Same JSON shape.")


# ---- Localization ----

def predict(d: Design, url: str, S: dict, prompt: str, known: dict):
    """predict -> force -> feedback until the failing check passes. Returns (accepted or None, last pred, rounds)."""
    messages, pred = [{"role": "user", "content": prompt}], None
    for rnd in range(1, S["rounds"] + 1):
        pred = parse_prediction(d, ask(url, S, messages), known)
        forced = run_forced(d, to_force(d, pred))
        logger.info("Localizer round %d: %s", rnd, "check passes" if forced["fixed"] else forced["target"])
        if forced["fixed"]:
            return pred, pred, rnd
        messages += [{"role": "assistant", "content": json.dumps(pred)},
                     {"role": "user", "content": feedback(d, pred, forced)}]
    return None, pred, S["rounds"]


def minimize(d: Design, accepted: dict):
    """Drop predicted changes one by one (latest cycle, nearest the output first) while the check still passes."""
    def run(cells):
        vals = {}
        for s, k in cells:
            vals.setdefault(s, {})[k] = int(accepted[s][f"c{k}"])
        return run_forced(d, vals)
    keep = [(s, k) for s, per in to_force(d, accepted).items() for k, v in per.items() if str(v) != d.waves[s][k]]
    for cell in sorted(keep, key=lambda x: (-x[1], d.signals.index(x[0]))):
        trial = [x for x in keep if x != cell]
        if run(trial)["fixed"]:
            keep = trial
    return keep, run(keep)


def driver_lines(tracer: VerilogTracer, sig: str) -> list[int]:
    """Lines that decide `sig`: its assignments, the if/case conditions around them, and the always header."""
    lines = {l for l, _ in tracer.assign_map.get(sig, [])}
    def names(n):
        if isinstance(n, vast.Identifier): return {n.name}
        if isinstance(n, (vast.Lvalue, vast.Pointer, vast.Partselect)): return names(n.var)
        if isinstance(n, vast.Concat): return set().union(*(names(x) for x in n.list))
        return set()
    def walk(n, ctx):
        if isinstance(n, (vast.BlockingSubstitution, vast.NonblockingSubstitution)):
            if sig in names(n.left): lines.update(ctx | {n.lineno})
            return
        if isinstance(n, (vast.IfStatement, vast.CaseStatement, vast.CasexStatement, vast.CasezStatement, vast.Always)):
            ctx = ctx | {n.lineno}
        for ch in n.children():
            walk(ch, ctx)
    walk(tracer.ast, frozenset())
    return sorted(lines)


def find_suspects(d: Design, final: dict, confirm: bool = True) -> list[dict]:
    """Signals wrong while their path inputs are right (a register's at the cycle before)"""
    pv = lambda s, c: final.get(s, {}).get(f"c{c}", "unknown")
    wrong = lambda s, c: pv(s, c).isdigit() and d.waves[s][c] != pv(s, c)
    suspects = []
    for c in d.cycles:
        for s in d.signals:
            cin = c - 1 if d.is_reg(s) else c
            bad_in = [p for p in d.preds(s) if p in d.signals and p != s and cin in d.cycles and wrong(p, cin)]
            if wrong(s, c) and not bad_in and all(x["signal"] != s for x in suspects):
                suspects.append(dict(signal=s, cycle=c, actual=d.waves[s][c], expected=pv(s, c),
                                     lines=driver_lines(d.tracer, s), early=cin not in d.cycles))
    for x in suspects:
        if not confirm:
            x["confirmed"] = "unchecked"
            continue
        x["confirmed"] = None if x["signal"] in d.meta["outputs"] else run_forced(d, to_force(d, final, [x["signal"]]))["fixed"]
    return suspects


def localize(bug_path: str, tb_path: str, description: str = "", targets: list[str] | None = None,
             deps: str = "", settings: dict | None = None, url: str | None = None,
             tracer: VerilogTracer | None = None) -> dict:
    """Run predict -> force -> feedback, keep the needed changes, walk back to the first wrong signal.
    targets: failing outputs (debug_agent's buggy_signals); deps: the tracer's dependency context text;
    tracer: the agent's VerilogTracer for `bug_path`.

    Returns {"suspects": [...], "lines": [...], "accepted": bool, "rounds": n, "prompt_block": str, ...};
    {"skipped": reason, "prompt_block": ""} when there is nothing to localize.
    """
    S, t0 = settings or load_settings(), time.time()
    usage0 = dict(USAGE)
    d = load_design(bug_path, tb_path, tracer)
    if isinstance(d, str):
        return dict(skipped=d, prompt_block="")
    choose_cells(d, [o for o in (targets or d.meta["outputs"]) if o in d.waves], S["max_signals"], S["window"])
    if not d.signals:
        return dict(skipped="no path signals", prompt_block="")
    d.layouts, d.port_width = learn_layouts(d) if d.outs else [], port_widths(d)
    known = known_cells(d)
    wiring = propagate_wiring(d, known)
    prompt = build_prompt(d, description, deps if S["use_deps"] else "", known)

    if S["force"]:
        accepted, pred, rounds = predict(d, url or ensure_server(S), S, prompt, known)
        minimal, run = minimize(d, accepted) if accepted else (None, None)
        final = ({s: {f"c{c}": run["sim"]["waves"][s][c] for c in d.cycles} for s in d.signals}
                 if run and run["fixed"] else accepted or pred)
        suspects = find_suspects(d, final)
    else:   # ablation: one prediction, nothing forced; suspects from where it disagrees with the waveform
        pred = parse_prediction(d, ask(url or ensure_server(S), S, [{"role": "user", "content": prompt}]), known)
        accepted, rounds, minimal = None, 1, None
        suspects = find_suspects(d, pred, confirm=False)

    result = dict(accepted=accepted is not None, forced=S["force"], rounds=rounds, signals=d.signals, cycles=d.cycles,
                  needed_changes=[f"{s} c{k}" for s, k in minimal or []], suspects=suspects,
                  wiring=[f"{x} <- {y}" for x, y in wiring], simulations=d.sims, seconds=round(time.time() - t0, 1),
                  lines=sorted({l for x in suspects for l in x["lines"]}),
                  **{k: USAGE[k] - usage0[k] for k in USAGE})   # this run's LLM usage
    result["prompt_block"] = format_block(result, open(bug_path).read())
    return result


def format_block(result: dict, src: str) -> str:
    """Suspicious-lines section for the refinement prompt."""
    sus = result.get("suspects") or []
    if not sus:
        return ""
    code = src.splitlines()
    if not result.get("forced", True):
        out = ["A separate localizer predicted what the signals on the failing path should be (the prediction was "
               "NOT checked in simulation, so these are UNCONFIRMED hints), then walked the dependency graph back "
               "to the first signal that is wrong while its inputs are right:"]
        return _suspect_lines(out, sus, code)
    how = (f"a forced prediction made the failing check pass (round {result['rounds']}), and only the changes "
           f"the check needs were kept" if result["accepted"] else
           "no prediction made the failing check pass, so these are UNCONFIRMED hints")
    out = ["A separate localizer predicted what the signals on the failing path should be, forced those values "
           f"into the simulation ({how}), then walked the dependency graph back to the first signal that is wrong "
           "while its inputs are right:"]
    return _suspect_lines(out, sus, code)


def _suspect_lines(out: list, sus: list, code: list) -> str:
    notes = {True: "forcing only this signal makes the failing check pass",
             False: "forcing only this signal is not enough on its own", None: "output port",
             "unchecked": "not checked in simulation"}
    for x in sorted(sus, key=lambda x: x["confirmed"] is not True):
        out.append(f"- {x['signal']} at c{x['cycle']}: is {x['actual']}, should be {x['expected']} ({notes[x['confirmed']]}"
                   + ("; already wrong at the start of the window, the origin may be earlier" if x["early"] else "") + ")")
        out += [f"    {l:3d}| {code[l - 1]}" for l in x["lines"] if 0 < l <= len(code)]
    out.append("Check these lines first. They are where the wrong value first appears; the fix may still be "
               "elsewhere if the prediction was wrong.")
    return "\n".join(out)
