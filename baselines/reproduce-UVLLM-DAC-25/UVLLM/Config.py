import os

PROJECT_PATH = os.path.join(os.path.abspath(os.path.dirname(__file__)), "..")

TIMEOUT = int(os.environ.get("UVLLM_TIMEOUT", 10))

REPAIR_TIME = 7200
# Benchmark folders (ErrorSet/, MEIC/) live at the repo root; conf paths are relative to it.
BENCHMARKS = os.path.normpath(PROJECT_PATH)
WORK_DIR = os.path.abspath(os.environ.get("UVLLM_WORK_DIR", os.path.join(PROJECT_PATH, "workdir")))
os.makedirs(WORK_DIR, exist_ok=True) 
PATCH_DIR = os.path.join(PROJECT_PATH, "patch")

LOG_PATH = os.environ.get("UVLLM_LOG_PATH", os.path.join(PROJECT_PATH, "logs/UVLLM.log"))
