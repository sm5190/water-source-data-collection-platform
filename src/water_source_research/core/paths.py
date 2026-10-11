from pathlib import Path

# Repository root (src/water_source_research/core/paths.py -> repo). Valid for the editable
# install used in development; config files live in <repo>/config.
REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config"
