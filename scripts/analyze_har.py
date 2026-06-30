#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analyzer.har_parser import export_har_analysis


def main() -> None:
    har_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Users\6\Desktop\测试.har")
    schema_dir = ROOT / "captures" / "schema"
    result = export_har_analysis(har_path, schema_dir)
    print(json.dumps(result["template"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
