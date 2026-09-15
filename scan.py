#!/usr/bin/env python3
"""Run a single scan cycle. `python3 scan.py`"""
import sys, json
from ct.engine import Engine

if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "paper.db"
    eng = Engine(db_path=db)
    stats = eng.scan()
    print("\n" + json.dumps(stats, indent=2))
