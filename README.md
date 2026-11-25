# RTLDetective

**Agentic RTL Debugging through Dataflow Analysis and Simulation-Guided Fault Localization** [[Paper]](https://arxiv.org/abs/2611.31709)

Neusha Javidnia, Ruisi Zhang, Farinaz Koushanfar 

RTLDetective finds and fixes functional and syntax bugs in Verilog RTL using a moderately sized,
locally hosted open-weight LLM. It needs no fine-tuning and no golden reference model. Instead
of only reasoning over code and traces, it tests its hypotheses in simulation: a small
**Localizer Agent** predicts what internal signals should be, RTLDetective forces those values into
the simulation to see whether the failure goes away, and the confirmed root cause is handed to a
**Debug Agent** that writes the repair.

On the MEIC benchmark (194 bugs), RTLDetective fixes **86.1%** of the bugs, **13.9 points** above
the best baseline, with every method using the same Qwen3-Coder-30B-A3B model. 

![RTLDetective overview](docs/figure1_overview.png)

*Figure 1. Overview of RTLDetective. Each iteration evaluates the current RTL candidate through
compilation, static analysis and simulation. Context Extraction builds a signal dependency graph,
traces dependencies backward from failing outputs, and summarizes the waveforms leading up to the
first failure. The Localizer Agent predicts expected values of intermediate signals and tests them
through counterfactual simulation. Root Cause Analysis uses the validated predictions to find
suspicious logic. The Debug Agent uses the localized fault information and the RTL debugging
knowledge base to generate the next repair candidate.*

## Contents

- [How it works](#how-it-works)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Run the full benchmark](#run-the-full-benchmark)
- [Configuration](#configuration)
- [Repository layout](#repository-layout)
- [Troubleshooting](#troubleshooting)
- [Citation](#citation)

## How it works

Each repair iteration runs the steps below. The links point to where each one is implemented.

| Step | What it does | Code |
|---|---|---|
| Compile, simulate, lint | Compiles with Icarus Verilog, runs the testbench, and applies 8 rule-based checks for common RTL bug patterns | [`tools/benchmark_runner.py`](tools/benchmark_runner.py), [`tools/static_analyzer.py`](tools/static_analyzer.py) |
| Dependency graph | Builds a hierarchical signal dependency graph across module boundaries and traces each failing output back to its drivers | [`agent/verilog_tracer.py`](agent/verilog_tracer.py) |
| Waveform analysis | Reduces the VCD to a compact per-cycle table of the signals on those paths, from the last passing check to the first failing one | [`agent/waveform.py`](agent/waveform.py) |
| Counterfactual fault localization | The Localizer Agent predicts the expected values of intermediate signals. RTLDetective forces them with `force`/`release` and re-simulates, refining for up to N rounds. | [`agent/predict_force.py`](agent/predict_force.py) |
| Knowledge-guided repair | The Debug Agent gets the spec, code, logs, waveform table and suspicious lines, plus a curated knowledge base (about 29 KB) of RTL bug and fix patterns.| [`agent/debug_agent.py`](agent/debug_agent.py), [`memory/`](memory/), [`skills/SKILL.md`](skills/SKILL.md) |
| Rollback | Scores each candidate by compilation and testbench pass rate and keeps the best version seen so far. | [`agent/debug_agent.py`](agent/debug_agent.py) |

The conversation is kept across iterations and served by vLLM with prefix caching, so the shared
prompt (knowledge base and earlier attempts) is not recomputed on every call.

## Installation

### Requirements

- **Linux** with Python 3.10 or newer.
- **[Icarus Verilog](https://steveicarus.github.io/iverilog/)**. The paper used version 13.0.
- **GPUs.** The paper used three NVIDIA RTX A6000 (48 GB) GPUs: two for the Debug Agent
  (Qwen3-Coder-30B-A3B, about 61 GB of weights) and one for the Localizer Agent (Qwen3-8B). The
  default `config.yaml` assumes exactly this layout. See [step 4](#4-match-the-config-to-your-gpus)
  for other setups.
- **Disk.** About 80 GB for the two models.

### 1. Clone

```bash
git clone https://github.com/ACESLabUCSD/RTLDetective.git
```

```bash
cd RTLDetective
```

### 2. Install Icarus Verilog

```bash
sudo apt install iverilog
```

```bash
iverilog -V | head -1
```

### 3. Create the Python environment

This creates a virtual environment in `./verilog_debug/` and installs `requirements.txt`, vLLM,
transformers and accelerate:

```bash
./run.sh install-vllm
```

```bash
source verilog_debug/bin/activate
```

```bash
./run.sh check-env
```

### 4. Match the config to your GPUs

The relevant settings in [`config.yaml`](config.yaml):

| Setting | Default | Meaning |
|---|---|---|
| `local_model.cache_dir` | `~/.cache/verilog_debug_models` | Where model weights are downloaded |
| `local_model.gpu_ids` | `"0,1,2"` | GPUs visible to the Debug Agent's server. It uses tensor parallelism over the largest power of two of them, so GPUs 0 and 1 |
| `localizer.gpu_ids` | `"2"` | GPU for the Localizer Agent's server |
| `vllm.gpu_memory_utilization` | `0.85` | Share of each Debug Agent GPU that vLLM reserves |
| `localizer.gpu_memory_utilization` | `0.6` | Share of the Localizer GPU that vLLM reserves |


## Quick start

Debug one functional bug with the full pipeline. The first run downloads the models and starts
both vLLM servers, which takes several minutes. Later runs reuse the running servers.

```bash
python -m agent.debug_agent testset/MEIC_Testset/accu/buggy/accu_func_logic_error.v \
    --testbench testset/MEIC_Testset/accu/benchmarks/tb.v \
    --model qwen-coder-30b --backend vllm \
    --waveform --localize --max-loops 3 \
    --output-dir output/quickstart
```

The design specification (`design_description.txt`) is picked up automatically from the design's
folder. The output folder then contains:

| File | Contents |
|---|---|
| `<bug>_fixed.v` | The repaired RTL (the best version found) |
| `report_<bug>_<time>.json` | Final pass rate, whether all tests pass, the best iteration and rollbacks, and for each iteration the testbench result, localizer result, latency and token usage. Use `--output FILE` to choose the name |

Two commands useful for checking the setup:

```bash
./run.sh lint testset/MEIC_Testset/accu/buggy/accu_func_logic_error.v
```

```bash
./run.sh bench testset/MEIC_Testset/accu/buggy/accu_func_logic_error.v testset/MEIC_Testset/accu/benchmarks/tb.v
```

The first runs only the static analyzer. The second runs the testbench on the buggy design and
prints its pass rate.

## Run the full benchmark

[`testset/MEIC_Testset/`](testset/MEIC_Testset/) is the extended MEIC benchmark: 194 buggy
instances (113 syntax, 81 functional) across 15 RTLLM designs. Each design folder has:

```
<design>/
├── design_description.txt   # natural-language specification
├── <design>.v               # reference implementation
├── benchmarks/tb.v          # self-checking testbench
└── buggy/<design>_{syn,func}_<bug>.v
```

A bug counts as **fixed** only when the final RTL passes **all** testbench checks.

[`run_MEIC_batch.sh`](run_MEIC_batch.sh) runs the full pipeline on every buggy file, one at a
time (vLLM backend, waveform analysis and localizer on). With 3 iterations per bug (Table I, row
"RTLDetective, 3 iter."):

```bash
./run_MEIC_batch.sh --max-loops 3 --seed 0 --output-dir output/rtldetective
```

Each bug produces `<output-dir>/<design>/<bug>_report.json`, `<bug>_log.txt` and `<bug>_fixed.v`.
The script prints the number of fixed bugs at the end. If a run is interrupted, rerun the same
command with `--resume` to skip bugs that already have a report.

| Option | Effect |
|---|---|
| `--max-loops N` | Repair iterations per bug (default 3) |
| `--seed N` | Base sampling seed. Each request uses seed + request number |
| `--modules accu alu` | Run only these designs |
| `--resume` | Skip bugs that already have a report |
| `--timeout S` | Per-bug time limit in seconds (default 3600) |
| `--save-prompts` | Also save every LLM request and reply to `<bug>_prompts.jsonl` |

### Paper Results (Table I of the paper)

| Method | Budget | Syntax FR | Functional FR | Overall |
|---|---|---|---|---|
| Qwen, no agent | 1 call | 44.2% | 35.8% | 79/194 (40.7%) |
| UVLLM | 4+4 iter. | 61.9% | 54.3% | 114/194 (58.8%) |
| MEIC | 3 iter. | 74.3% | 63.0% | 135/194 (69.6%) |
| MEIC | 10 iter. | 74.3% | 69.1% | 140/194 (72.2%) |
| **RTLDetective** | 3 iter. | 87.6% | 71.6% | 157/194 (80.9%) |
| **RTLDetective** | 10 iter. | **93.8%** | **74.1%** | **167/194 (86.1%)** |

## Configuration

[`config.yaml`](config.yaml) controls the models and servers:

- **`vllm:`** Debug Agent server: port (8000), sampling (temperature, top-p, repetition penalty,
  seed, max tokens), tensor parallelism and context length. Sampling changes take effect on the next
  request. Server changes take effect after a restart.
- **`localizer:`** Localizer Agent: model, port (8001), GPU, number of predict-and-force rounds
  (`rounds`), cycle window (`window`) and number of signals to predict (`max_signals`).
- **`agent.max_loops`:** default number of repair iterations.
- **`simulation:`, `evaluation:`:** simulator and timeouts.

The same agent can also run on HuggingFace transformers (`--backend hf`, installed with
`./run.sh install-local`) or on remote models through OpenRouter (`--backend openrouter`, after
copying `.env.example` to `.env` and setting `OPENROUTER_API_KEY`). `./run.sh models` lists the
model shortcuts. The paper's results were produced with the vLLM backend only.

## Repository layout

```
RTLDetective/
├── run_MEIC_batch.sh       # Runs the full benchmark
├── run.sh                  # Helper: install, check-env, lint, bench, debug, models, ...
├── config.yaml             # Models, vLLM servers, localizer and simulator settings
├── agent/
│   ├── debug_agent.py      # Debug Agent: iterative repair loop with rollback
│   ├── predict_force.py    # Localizer Agent: predict, force, re-simulate, root cause analysis
│   ├── verilog_tracer.py   # Signal dependency graph (Pyverilog)
│   ├── waveform.py         # VCD to compact transition table
│   ├── memory_loader.py    # Loads the knowledge base into the system prompt
│   └── backends/           # vLLM server and client, HuggingFace and OpenRouter clients
├── tools/                  # Static analyzer, testbench runner
├── memory/                 # Knowledge base: bug and fix patterns, Verilog references, templates
├── skills/SKILL.md         # Debugging procedure given to the Debug Agent
├── testset/MEIC_Testset/   # Benchmark: 15 designs, 194 bugs
├── testset/ACES/           # Larger multi-file designs
├── samples/                # Small example bugs and testbenches
├── scripts/                # Batch run and report evaluation for samples/
└── docs/                   # Figures
```

Everything the agent produces goes to `output/` and `logs/`.

## Citation

If you find this work useful, please cite our paper:

```bibtex
@article{javidnia2026rtldetective,
  title         = {RTLDetective: Agentic RTL Debugging through Dataflow Analysis and Simulation-Guided Fault Localization},
  author        = {Javidnia, Neusha and Zhang, Ruisi and Koushanfar, Farinaz},
  journal       = {arXiv preprint arXiv:2611.31709},
  year          = {2026},
  eprint        = {2611.31709},
  archivePrefix = {arXiv},
  url           = {https://arxiv.org/abs/2611.31709}
}
```
