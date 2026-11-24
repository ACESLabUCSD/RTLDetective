"""config.yaml access shared by the backends (kept separate to avoid import cycles)."""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def load_config_section(section: str) -> dict:
    """Read one top-level section from config.yaml (if it exists).
    The env var VDEBUG_SEED overrides vllm.seed (the localizer inherits it unless it sets its own)."""
    sec = {}
    if CONFIG_PATH.exists():
        try:
            import yaml
            with open(CONFIG_PATH) as f:
                cfg = yaml.safe_load(f) or {}
            sec = dict(cfg.get(section) or {})
        except Exception as e:
            logger.warning("Could not read config.yaml: %s", e)
    if section == "vllm" and os.environ.get("VDEBUG_SEED", "").strip():
        sec["seed"] = int(os.environ["VDEBUG_SEED"])
    return sec
