#!/usr/bin/env python3
"""Launch Engine 3 (Buyma list) — independent process."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from apps.engine3_buyma.app import main

if __name__ == "__main__":
    main()
