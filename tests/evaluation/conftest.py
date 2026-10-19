import sys
from pathlib import Path

# Shared helpers live in tests/ (and tests/evaluation/).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
