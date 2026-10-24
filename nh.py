#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""便捷入口：python nh.py <命令>  （等价于 python -m novelhub）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from novelhub.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
