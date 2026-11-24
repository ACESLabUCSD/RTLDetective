#!/usr/bin/env bash
#
# run_MEIC_batch.sh — Run the debug agent on every MEIC buggy file.
#
# For each buggy file like:
#   testset/MEIC_Testset/accu/buggy/accu_func_improper_sensignal.v
# it runs the agent and writes output to:
#   output/MEIC_Testset/accu/accu_func_improper_sensignal_report.json
#   output/MEIC_Testset/accu/accu_func_improper_sensignal_log.txt
#   output/MEIC_Testset/accu/accu_func_improper_sensignal_fixed.v
#
# Usage:
#   ./run_MEIC_batch.sh                        # all modules, default model
#   ./run_MEIC_batch.sh --model qwen-coder-30b
#   ./run_MEIC_batch.sh --backend vllm         # hf (default), vllm, openrouter
#   ./run_MEIC_batch.sh --modules accu alu     # only selected modules
#   ./run_MEIC_batch.sh --resume               # skip files that already have a report
#   ./run_MEIC_batch.sh --max-loops 5
#   ./run_MEIC_batch.sh --timeout 600
#   ./run_MEIC_batch.sh --output-dir output/MEIC_Testset  # custom output dir
#   ./run_MEIC_batch.sh --waveform             # add the waveform table to prompts (default: off)
#   ./run_MEIC_batch.sh --localize             # add predict-and-force suspicious lines to refinement prompts
#   ./run_MEIC_batch.sh --save-prompts         # also save every LLM request/reply to <stem>_prompts.jsonl
#
# With --backend vllm the script starts one shared `vllm serve` (prefix caching on)
# before the first file and stops it when the batch ends, also on Ctrl-C or an error.
# If a server is already running it is reused and left running.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

TESTSET="testset/MEIC_Testset"
OUTPUT_BASE="output/MEIC_Testset"
MODEL="qwen-coder-30b"
BACKEND="vllm"  # hf, vllm, openrouter
MAX_LOOPS=3
TIMEOUT=3600
RESUME=false
MODULES=()
SEED=""          # base sampling seed; empty = vllm: seed in config.yaml; set by --seed
WAVEFORM="--waveform"  # set to --waveform by the --waveform option
LOCALIZE="--localize"  # set to --localize by the --localize option
SAVE_PROMPTS=""        # set to --save-prompts by the --save-prompts option (default: off)

# ── Parse arguments ──────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --model|-m)      MODEL="$2"; shift 2 ;;
        --backend|-b)    BACKEND="$2"; shift 2 ;;
        --max-loops|-L)  MAX_LOOPS="$2"; shift 2 ;;
        --timeout)       TIMEOUT="$2"; shift 2 ;;
        --resume)        RESUME=true; shift ;;
        --modules)       shift; while [[ $# -gt 0 && ! "$1" =~ ^-- ]]; do MODULES+=("$1"); shift; done ;;
        --output-dir)    OUTPUT_BASE="$2"; shift 2 ;;
        --seed)          SEED="$2"; shift 2 ;;
        --waveform)      WAVEFORM="--waveform"; shift ;;
        --localize)      LOCALIZE="--localize"; shift ;;
        --save-prompts)  SAVE_PROMPTS="--save-prompts"; shift ;;
        --no-waveform)   WAVEFORM=""; shift ;;
        --no-localize)   LOCALIZE=""; shift ;;
        -h|--help)
            echo "Usage: $0 [--model M] [--backend hf|vllm|openrouter] [--max-loops N] [--seed N] [--timeout S] [--resume] [--modules m1 m2 ...]"
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done
if [[ -n "$SEED" ]]; then
    [[ "$SEED" =~ ^[0-9]+$ ]] || { echo "--seed must be a non-negative integer" >&2; exit 1; }
    export VDEBUG_SEED="$SEED"   # read by agent/backends/config.py (main model and localizer)
fi

# ── Activate venv if present ─────────────────────────────────────────
VENV_DIR="${SCRIPT_DIR}/verilog_debug"
if [[ -d "$VENV_DIR" && ( -z "${VIRTUAL_ENV:-}" || "$VIRTUAL_ENV" != "$VENV_DIR" ) ]]; then
    source "${VENV_DIR}/bin/activate"
fi

# ── Set CUDA_VISIBLE_DEVICES from config.yaml if not already set ─────
if [[ -z "${CUDA_VISIBLE_DEVICES:-}" && -f "${SCRIPT_DIR}/config.yaml" ]]; then
    gpu_ids=$(python3 -c "
import yaml
with open('${SCRIPT_DIR}/config.yaml') as f:
    cfg = yaml.safe_load(f) or {}
v = cfg.get('local_model', {}).get('gpu_ids', '')
if v is not None and str(v).strip():
    print(str(v).strip())
" 2>/dev/null)
    if [[ -n "$gpu_ids" ]]; then
        export CUDA_VISIBLE_DEVICES="$gpu_ids"
        echo "Set CUDA_VISIBLE_DEVICES=$gpu_ids (from config.yaml)"
    fi
fi

# ── vLLM server: start once for the whole batch, stop at the end ─────
STARTED_VLLM=false
stop_vllm() {
    if $STARTED_VLLM; then
        echo ""
        echo "Stopping vLLM server..."
        python3 -m agent.backends.vllm_server stop || true
        STARTED_VLLM=false
    fi
}
if [[ "$BACKEND" == "vllm" ]]; then
    trap stop_vllm EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    echo "Starting vLLM server for $MODEL (log: logs/vllm_server.log)..."
    if ! server_out=$(python3 -m agent.backends.vllm_server start --model "$MODEL" 2>&1); then
        echo "$server_out" | tail -n 25
        echo "ERROR: vLLM server did not start — not running any files."
        exit 1
    fi
    server_line=$(echo "$server_out" | tail -n 1)
    if [[ "$server_line" == started* ]]; then
        STARTED_VLLM=true
        echo "  vLLM server started: ${server_line#started }"
    else
        echo "  vLLM server already running, reusing it: ${server_line#running }"
    fi
    export VLLM_KEEP_SERVER=1
fi

# ── Collect buggy files ──────────────────────────────────────────────
files=()
for module_dir in "$TESTSET"/*/; do
    module="$(basename "$module_dir")"

    # Filter by --modules if specified
    if [[ ${#MODULES[@]} -gt 0 ]]; then
        skip=true
        for m in "${MODULES[@]}"; do
            [[ "$m" == "$module" ]] && skip=false && break
        done
        $skip && continue
    fi

    tb="${module_dir}benchmarks/tb.v"
    [[ -f "$tb" ]] || continue

    for vf in "${module_dir}buggy/"*.v; do
        [[ -f "$vf" ]] || continue
        # Skip intermediate artifacts left by interrupted runs
        [[ "$(basename "$vf")" == *_iter[0-9]*.v ]] && continue
        files+=("$vf")
    done
done

total=${#files[@]}
if [[ $total -eq 0 ]]; then
    echo "No buggy files found. Check ${TESTSET}/ directory."
    exit 1
fi

# ── Banner ───────────────────────────────────────────────────────────
echo "MEIC Batch Runner (bash)"
echo "  Model:      $MODEL"
echo "  Backend:    $BACKEND"
echo "  Max loops:  $MAX_LOOPS"
echo "  Seed:       ${SEED:-config.yaml}"
echo "  Timeout:    ${TIMEOUT}s per file"
echo "  Files:      $total"
echo "  Resume:     $RESUME"
echo "  Waveform:   ${WAVEFORM:-off}"
echo "  Localize:   ${LOCALIZE:-off}"
echo "  Save prompts: ${SAVE_PROMPTS:-off}"
echo "======================================================================"

passed=0
failed=0
errors=0
skipped=0
idx=0
start_time=$SECONDS

# ── Main loop ────────────────────────────────────────────────────────
for vf in "${files[@]}"; do
    idx=$((idx + 1))
    stem="$(basename "$vf" .v)"
    module_dir="$(dirname "$(dirname "$vf")")"
    module="$(basename "$module_dir")"
    tb="${module_dir}/benchmarks/tb.v"
    out_dir="${OUTPUT_BASE}/${module}"
    mkdir -p "$out_dir"
    report="${out_dir}/${stem}_report.json"

    printf "\n[%d/%d] %s/%s\n" "$idx" "$total" "$module" "$stem"

    # Resume: skip if report already exists and looks valid
    if $RESUME && [[ -f "$report" ]]; then
        if python3 -c "
import json, sys
r = json.load(open('$report'))
ap = r.get('evaluation',{}).get('all_pass', False)
print('PASS' if ap else 'FAIL')
sys.exit(0)
" 2>/dev/null; then
            prev=$(python3 -c "
import json
r = json.load(open('$report'))
print('PASS' if r.get('evaluation',{}).get('all_pass') else 'FAIL')
")
            echo "  SKIP (resume) — previous: $prev"
            skipped=$((skipped + 1))
            [[ "$prev" == "PASS" ]] && passed=$((passed + 1)) || failed=$((failed + 1))
            continue
        fi
    fi

    # Run the debug agent with a timeout
    t0=$SECONDS
    timeout "${TIMEOUT}s" python3 -m agent.debug_agent \
        "$vf" \
        --testbench "$tb" \
        --model "$MODEL" \
        ${BACKEND:+--backend "$BACKEND"} \
        ${WAVEFORM:+"$WAVEFORM"} \
        ${LOCALIZE:+"$LOCALIZE"} \
        ${SAVE_PROMPTS:+"$SAVE_PROMPTS"} \
        --backend "$BACKEND" \
        --max-loops "$MAX_LOOPS" \
        --output "$report" \
        --output-dir "$out_dir" \
        > "${out_dir}/${stem}_log.txt" 2>&1
    rc=$?
    elapsed=$(( SECONDS - t0 ))

    # Classify result
    if [[ $rc -eq 124 ]]; then
        echo "  TIMEOUT  [${elapsed}s]"
        errors=$((errors + 1))
    elif [[ $rc -ne 0 ]]; then
        echo "  ERROR (exit $rc)  [${elapsed}s]"
        errors=$((errors + 1))
    elif [[ -f "$report" ]]; then
        all_pass=$(python3 -c "
import json
r = json.load(open('$report'))
print('yes' if r.get('evaluation',{}).get('all_pass') else 'no')
" 2>/dev/null || echo "no")
        if [[ "$all_pass" == "yes" ]]; then
            echo "  PASS  [${elapsed}s]"
            passed=$((passed + 1))
        else
            rate=$(python3 -c "
import json
r = json.load(open('$report'))
pr = r.get('evaluation',{}).get('final_pass_rate')
print(f'{pr:.0%}' if pr is not None else 'N/A')
" 2>/dev/null || echo "N/A")
            echo "  FAIL (pass_rate=$rate)  [${elapsed}s]"
            failed=$((failed + 1))
        fi
    else
        echo "  ERROR (no report)  [${elapsed}s]"
        errors=$((errors + 1))
    fi
done

# ── Summary ──────────────────────────────────────────────────────────
batch_elapsed=$(( SECONDS - start_time ))
echo ""
echo "======================================================================"
echo "MEIC Batch Complete"
echo "  Total: $total  PASS: $passed  FAIL: $failed  ERROR: $errors  SKIP: $skipped"
if [[ $total -gt 0 ]]; then
    echo "  Pass rate: ${passed}/${total}"
fi
echo "  Elapsed: ${batch_elapsed}s ($((batch_elapsed / 60))min)"
echo ""
echo "  Output files are organized under:"
echo "    ${OUTPUT_BASE}/<module>/<stem>_report.json"
echo "    ${OUTPUT_BASE}/<module>/<stem>_log.txt"
echo "    ${OUTPUT_BASE}/<module>/<stem>_fixed.v"
echo "======================================================================"
