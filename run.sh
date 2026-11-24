#!/usr/bin/env bash
#
# run.sh — Convenience wrapper for the Verilog Debug Agent
#
# Usage:
#   ./run.sh debug <file.v> [options]       Debug a single Verilog file
#   ./run.sh debug-local <file.v> [options]  Debug using local Qwen model (no API key)
#   ./run.sh meic <module> <file> [options]  Debug one MEIC testset buggy file
#   ./run.sh meic-batch [options]            Batch-run all MEIC testset files
#   ./run.sh lint <file.v>                   Static analysis only (no LLM)
#   ./run.sh bench <file.v> [tb.v]           Run testbench and show pass rate
#   ./run.sh sim <file.v> [--testbench tb.v] Compile & simulate
#   ./run.sh batch [options]                 Run agent on all sample files
#   ./run.sh evaluate [options]              Evaluate reports by testbench pass rate
#   ./run.sh models                          List available LLM models
#   ./run.sh check-env                       Verify environment setup
#   ./run.sh install                         Install Python dependencies
#   ./run.sh install-local                   Install deps for local model inference
#   ./run.sh install-vllm                    Install deps for vLLM inference

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load .env if present
if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

VENV_NAME="verilog_debug"
VENV_DIR="${SCRIPT_DIR}/${VENV_NAME}"

# --- Virtual-environment helpers ---

ensure_venv_active() {
    # If the venv exists but isn't active, activate it transparently.
    if [[ -d "$VENV_DIR" ]] && [[ -z "${VIRTUAL_ENV:-}" || "$VIRTUAL_ENV" != "$VENV_DIR" ]]; then
        source "${VENV_DIR}/bin/activate"
    fi
}

create_venv() {
    if [[ -d "$VENV_DIR" ]]; then
        echo -e "${GREEN}Virtual environment '${VENV_NAME}' already exists.${NC}"
    else
        echo -e "${CYAN}Creating virtual environment '${VENV_NAME}'...${NC}"
        python3 -m venv "$VENV_DIR"
        echo -e "${GREEN}Created: ${VENV_DIR}${NC}"
    fi
    source "${VENV_DIR}/bin/activate"
    echo -e "${GREEN}Activated: ${VENV_NAME}${NC}"
    pip install --upgrade pip --quiet
}

# Auto-activate if the venv already exists (keeps every sub-command inside it)
ensure_venv_active

usage() {
    cat <<EOF
${CYAN}Verilog Debug Agent${NC}

Usage: ./run.sh <command> [arguments]

Commands:
  ${GREEN}debug${NC} <file.v> [opts]        Debug a Verilog file (remote LLM via OpenRouter)
  ${GREEN}debug-local${NC} <file.v> [opts]   Debug using local Qwen model (no API key)
                                  Use -m qwen-coder-30b (default) or -m qwen-coder-next
  ${GREEN}meic${NC} <module> <file> [opts]   Debug one MEIC testset buggy file
  ${GREEN}meic-batch${NC} [opts]             Batch-run all MEIC testset buggy files
  ${GREEN}lint${NC} <file.v>                 Run static analysis only (no LLM needed)
  ${GREEN}bench${NC} <file.v> [tb.v]          Run testbench and report pass rate
  ${GREEN}sim${NC} <file.v> [--testbench tb]  Compile and simulate with Icarus Verilog
  ${GREEN}batch${NC} [opts]                  Run debug agent on all files in samples/buggy/
  ${GREEN}evaluate${NC} [opts]               Evaluate output reports by testbench pass rate
  ${GREEN}models${NC}                        List available LLM models
  ${GREEN}check-env${NC}                     Verify environment setup
  ${GREEN}install${NC}                       Create venv & install dependencies (remote models)
  ${GREEN}install-local${NC}                 Create venv & install deps for local inference
  ${GREEN}install-vllm${NC}                  Create venv & install deps for vLLM inference

Debug options:
  -m, --model MODEL         Model name or shortcut (default: qwen-coder-30b)
  -b, --backend BACKEND     hf (default) | vllm | openrouter
  -t, --testbench FILE      Testbench file (auto-discovered if omitted)
  -L, --max-loops N         Max debugging iterations (default: 3)
  -o, --output FILE         Output report path
  -v, --verbose             Verbose logging

MEIC options (meic):
  -m, --model MODEL         Model shortcut (default: qwen-coder-30b)
  -b, --backend BACKEND     hf (default) | vllm | openrouter
  -L, --max-loops N         Max debugging iterations (default: 3)

MEIC batch options (meic-batch):
  --modules M1 M2 ...       Run only these modules (default: all)
  --model MODEL             Model shortcut (default: qwen-coder-30b)
  --backend BACKEND         hf (default) | vllm | openrouter
  --max-loops N             Max debugging iterations per file (default: 3)
  --timeout SECS            Timeout per file in seconds (default: 3600)
  --resume                  Skip files that already have results
  --output-dir DIR          Custom output directory (default: output/MEIC_Testset)

Evaluate options:
  --rerun                   Re-run testbenches on fixed files (instead of cached)
  --report-dir DIR          Directory of report files

Examples:
  ./run.sh debug samples/buggy/counter_4bit.v
  ./run.sh debug samples/buggy/alu_simple.v -m gpt-4o --max-loops 5
  ./run.sh debug-local samples/buggy/fsm_traffic.v
  ./run.sh debug-local samples/buggy/fsm_traffic.v -m qwen-coder-next
  ./run.sh meic accu accu_func_logic_error.v
  ./run.sh meic adder_32bit adder_32bit_syn_unexpected_end.v -m qwen-coder-next
  ./run.sh meic-batch
  ./run.sh meic-batch --modules accu alu --resume
  ./run.sh bench samples/buggy/counter_4bit.v
  ./run.sh lint samples/buggy/counter_4bit.v
  ./run.sh batch -m deepseek-chat
  ./run.sh evaluate --rerun
EOF
}

cmd_check_env() {
    echo -e "${CYAN}=== Environment Check ===${NC}"
    echo ""

    # Virtual environment
    if [[ -d "$VENV_DIR" ]]; then
        if [[ "${VIRTUAL_ENV:-}" == "$VENV_DIR" ]]; then
            echo -e "  Venv:           ${GREEN}${VENV_NAME} (active)${NC}"
        else
            echo -e "  Venv:           ${YELLOW}${VENV_NAME} (exists but not active)${NC}"
            echo -e "                  run: source ${VENV_NAME}/bin/activate"
        fi
    else
        echo -e "  Venv:           ${RED}not created${NC} (run: ./run.sh install)"
    fi

    # Python
    if command -v python3 &>/dev/null; then
        PY_VER=$(python3 --version 2>&1)
        echo -e "  Python:         ${GREEN}${PY_VER}${NC}"
    else
        echo -e "  Python:         ${RED}NOT FOUND${NC}"
    fi

    # pip packages (display_name:import_name)
    for entry in requests:requests pyyaml:yaml; do
        pkg="${entry%%:*}"
        mod="${entry##*:}"
        if python3 -c "import ${mod}" 2>/dev/null; then
            echo -e "  ${pkg}:$(printf '%*s' $((14 - ${#pkg})) '')${GREEN}installed${NC}"
        else
            echo -e "  ${pkg}:$(printf '%*s' $((14 - ${#pkg})) '')${RED}missing${NC} (run: ./run.sh install)"
        fi
    done

    # torch / transformers (optional)
    for pkg in torch transformers vllm; do
        if python3 -c "import ${pkg}" 2>/dev/null; then
            echo -e "  ${pkg}:$(printf '%*s' $((14 - ${#pkg})) '')${GREEN}installed${NC}"
        else
            echo -e "  ${pkg}:$(printf '%*s' $((14 - ${#pkg})) '')${YELLOW}not installed${NC} (needed for local models)"
        fi
    done

    # CUDA
    if python3 -c "import torch; assert torch.cuda.is_available()" 2>/dev/null; then
        GPU=$(python3 -c "import torch; print(torch.cuda.get_device_name(0))" 2>/dev/null)
        echo -e "  CUDA GPU:       ${GREEN}${GPU}${NC}"
    else
        echo -e "  CUDA GPU:       ${YELLOW}not available${NC} (local models will use CPU)"
    fi

    # Simulators
    if command -v iverilog &>/dev/null; then
        IV_VER=$(iverilog -V 2>&1 | head -1)
        echo -e "  Icarus Verilog: ${GREEN}${IV_VER}${NC}"
    else
        echo -e "  Icarus Verilog: ${YELLOW}not found${NC} (optional: apt install iverilog)"
    fi

    if command -v verilator &>/dev/null; then
        VL_VER=$(verilator --version 2>&1)
        echo -e "  Verilator:      ${GREEN}${VL_VER}${NC}"
    else
        echo -e "  Verilator:      ${YELLOW}not found${NC} (optional: apt install verilator)"
    fi

    # API key
    if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
        MASKED="${OPENROUTER_API_KEY:0:8}...${OPENROUTER_API_KEY: -4}"
        echo -e "  OpenRouter key: ${GREEN}set${NC} (${MASKED})"
    else
        echo -e "  OpenRouter key: ${YELLOW}not set${NC} (needed for remote models)"
    fi

    echo ""
}

cmd_install() {
    create_venv
    echo -e "${CYAN}Installing base dependencies...${NC}"
    pip install -r requirements.txt
    echo -e ""
    echo -e "${GREEN}Done! Activate the environment with:${NC}"
    echo -e "  source ${VENV_NAME}/bin/activate"
}

cmd_install_local() {
    create_venv
    echo -e "${CYAN}Installing dependencies for local model inference...${NC}"
    pip install -r requirements.txt
    # pip install torch --index-url https://download.pytorch.org/whl/rocm7.2 ##amd
    pip install torch transformers accelerate
    echo -e ""
    echo -e "${GREEN}Done! Activate the environment with:${NC}"
    echo -e "  source ${VENV_NAME}/bin/activate"
    echo -e "${YELLOW}Note: The Qwen3-Coder model (~16GB) will be downloaded on first use.${NC}"
}

cmd_install_vllm() {
    create_venv
    echo -e "${CYAN}Installing dependencies for vLLM inference...${NC}"
    pip install -r requirements.txt
    # vllm pins its own torch version, so install it before transformers/accelerate.
    pip install vllm
    pip install transformers accelerate
    echo -e ""
    echo -e "${GREEN}Done! Activate the environment with:${NC}"
    echo -e "  source ${VENV_NAME}/bin/activate"
    echo -e "${YELLOW}Run with --backend vllm, e.g. --model qwen-coder-30b --backend vllm${NC}"
}

cmd_debug() {
    if [[ $# -lt 1 ]]; then
        echo -e "${RED}Error: debug requires a Verilog file path${NC}"
        echo "Usage: ./run.sh debug <file.v> [options]"
        exit 1
    fi
    python3 -m agent.debug_agent "$@"
}

cmd_debug_local() {
    if [[ $# -lt 1 ]]; then
        echo -e "${RED}Error: debug-local requires a Verilog file path${NC}"
        echo "Usage: ./run.sh debug-local <file.v> [options]"
        echo "  Local models: qwen-coder-30b (default), qwen-coder-next"
        exit 1
    fi
    local has_model=false
    for arg in "$@"; do
        if [[ "$arg" == "--model" || "$arg" == "-m" ]]; then
            has_model=true
            break
        fi
    done
    if $has_model; then
        python3 -m agent.debug_agent "$@"
    else
        python3 -m agent.debug_agent "$@" --model qwen-coder-30b
    fi
}

cmd_meic() {
    if [[ $# -lt 2 ]]; then
        echo -e "${RED}Error: meic requires a module name and buggy filename${NC}"
        echo "Usage: ./run.sh meic <module> <buggy_file> [options]"
        echo ""
        echo "Arguments:"
        echo "  module       MEIC testset module (e.g. accu, adder_32bit, alu)"
        echo "  buggy_file   Filename inside the module's buggy/ directory"
        echo ""
        echo "Options:"
        echo "  -m, --model MODEL    Model shortcut (default: qwen-coder-30b)"
        echo "  -L, --max-loops N    Max debugging iterations (default: 3)"
        echo ""
        echo "Available modules:"
        if [[ -d "testset/MEIC_Testset" ]]; then
            ls -1 testset/MEIC_Testset/ | sed 's/^/  /'
        fi
        exit 1
    fi
    local module="$1"; shift
    local buggy_file="$1"; shift

    local buggy_path="testset/MEIC_Testset/${module}/buggy/${buggy_file}"
    local tb_path="testset/MEIC_Testset/${module}/benchmarks/tb.v"
    local out_dir="output/MEIC_Testset/${module}"

    if [[ ! -f "$buggy_path" ]]; then
        echo -e "${RED}Error: buggy file not found: ${buggy_path}${NC}"
        echo "Available buggy files for ${module}:"
        if [[ -d "testset/MEIC_Testset/${module}/buggy" ]]; then
            ls -1 "testset/MEIC_Testset/${module}/buggy/" | grep '\.v$' | sed 's/^/  /'
        fi
        exit 1
    fi
    if [[ ! -f "$tb_path" ]]; then
        echo -e "${RED}Error: testbench not found: ${tb_path}${NC}"
        exit 1
    fi

    mkdir -p "$out_dir"

    # Default to qwen-coder-30b unless the user passes -m / --model.
    local has_model=false
    for arg in "$@"; do
        if [[ "$arg" == "--model" || "$arg" == "-m" ]]; then
            has_model=true
            break
        fi
    done
    if $has_model; then
        python3 -m agent.debug_agent "$buggy_path" -t "$tb_path" --output-dir "$out_dir" "$@"
    else
        python3 -m agent.debug_agent "$buggy_path" -t "$tb_path" --output-dir "$out_dir" --model qwen-coder-30b "$@"
    fi
}

cmd_meic_batch() {
    bash run_MEIC_batch.sh "$@"
}

cmd_lint() {
    if [[ $# -lt 1 ]]; then
        echo -e "${RED}Error: lint requires a Verilog file path${NC}"
        echo "Usage: ./run.sh lint <file.v>"
        exit 1
    fi
    python3 tools/static_analyzer.py "$@"
}

cmd_bench() {
    if [[ $# -lt 1 ]]; then
        echo -e "${RED}Error: bench requires a Verilog file path${NC}"
        echo "Usage: ./run.sh bench <file.v> [<testbench.v>]"
        exit 1
    fi
    python3 tools/benchmark_runner.py "$@"
}

cmd_sim() {
    if [[ $# -lt 1 ]]; then
        echo -e "${RED}Error: sim requires a Verilog file path${NC}"
        echo "Usage: ./run.sh sim <file.v> [--testbench tb.v]"
        exit 1
    fi
    python3 tools/test_runner.py "$@"
}

cmd_batch() {
    python3 scripts/run_all.py "$@"
}

cmd_evaluate() {
    python3 scripts/evaluate.py "$@"
}

cmd_models() {
    python3 -m agent.debug_agent --list-models
}

# --- Main dispatch ---
if [[ $# -lt 1 ]]; then
    usage
    exit 0
fi

COMMAND="$1"
shift

case "$COMMAND" in
    debug)        cmd_debug "$@" ;;
    debug-local)  cmd_debug_local "$@" ;;
    meic)         cmd_meic "$@" ;;
    meic-batch)   cmd_meic_batch "$@" ;;
    lint)         cmd_lint "$@" ;;
    bench)        cmd_bench "$@" ;;
    sim)          cmd_sim "$@" ;;
    batch)        cmd_batch "$@" ;;
    evaluate)     cmd_evaluate "$@" ;;
    models)       cmd_models "$@" ;;
    check-env)    cmd_check_env ;;
    install)      cmd_install ;;
    install-local) cmd_install_local ;;
    install-vllm) cmd_install_vllm ;;
    help|-h|--help) usage ;;
    *)
        echo -e "${RED}Unknown command: ${COMMAND}${NC}"
        echo ""
        usage
        exit 1
        ;;
esac
