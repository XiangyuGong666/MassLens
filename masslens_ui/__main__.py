"""`python -m masslens_ui` 入口（等价于 python -m masslens_ui.server）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 项目根目录

from masslens_ui.server import main

main()
