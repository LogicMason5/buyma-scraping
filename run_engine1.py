#!/usr/bin/env python3
"""Launch Engine 1 (EC scrape) — independent process."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from apps.engine1_scrape.app import main

if __name__ == "__main__":
    main()
