import sys
from pathlib import Path

# Ensure repo root on sys.path (Windows / editable install fallback)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
