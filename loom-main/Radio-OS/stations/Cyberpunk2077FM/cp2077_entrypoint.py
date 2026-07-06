#!/usr/bin/env python3
"""
Night City FM — station entrypoint.
Adds the repo root to sys.path and delegates to bookmark.main().
"""
import sys
import os

parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

import bookmark

if __name__ == "__main__":
    bookmark.main()
