"""Make `src` importable without requiring an editable install.

Lets `pytest` run straight from a clean clone — useful on day one, and useful in CI
before dependencies are cached.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
