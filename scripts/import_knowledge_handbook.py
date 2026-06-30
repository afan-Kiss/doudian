#!/usr/bin/env python3
"""Import desktop 客服学习手册 xlsx into project data/ for bot knowledge."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = Path.home() / "Desktop"
TARGET = ROOT / "data" / "和田玉手镯客服学习手册.xlsx"


def find_source() -> Path | None:
    if len(sys.argv) > 1:
        candidate = Path(sys.argv[1]).expanduser()
        if candidate.exists():
            return candidate

    for path in DESKTOP.glob("*.xlsx"):
        if "学习手册" in path.name or "客服" in path.name:
            return path
        if path.stat().st_size == 19559:
            return path
    return None


def main() -> int:
    source = find_source()
    if not source:
        print("未找到手册文件。用法: python scripts/import_knowledge_handbook.py [xlsx路径]")
        return 1

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, TARGET)
    print(f"已导入: {source} -> {TARGET}")
    print("重启 chat_ui.py 后 AI 将自动加载该手册。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
