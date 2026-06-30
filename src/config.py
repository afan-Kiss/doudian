from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config.yaml"
ENV_PATH = ROOT_DIR / ".env"
ASSISTANT_ENV = ROOT_DIR.parent / ".env"


def load_dotenv(path: Path | None = None) -> None:
    for env_path in (path, ENV_PATH, ASSISTANT_ENV):
        if not env_path or not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def load_config(path: Path | None = None) -> dict[str, Any]:
    load_dotenv()
    config_path = path or CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    capture_dir = ROOT_DIR / config.get("capture_dir", "captures")
    profile_raw = (
        os.environ.get("DOUYIN_CDP_PROFILE")
        or config.get("browser", {}).get("user_data_dir")
        or str(ROOT_DIR.parent / "data" / "chrome-profile")
    )
    user_data_dir = Path(profile_raw)
    if not user_data_dir.is_absolute():
        user_data_dir = ROOT_DIR / user_data_dir

    browser = config.setdefault("browser", {})
    browser["debug_port"] = int(os.environ.get("CDP_PORT") or browser.get("debug_port") or 9222)
    browser["executable_path"] = os.environ.get("BROWSER_EXECUTABLE_PATH") or browser.get(
        "executable_path",
        r"C:\Users\1\AppData\Local\Google\Chrome\Application\chrome.exe",
    )
    browser["user_data_dir"] = str(user_data_dir)
    browser["connect_only"] = True
    browser.pop("channel", None)

    urls = config.setdefault("urls", {})
    if os.environ.get("DOUYIN_FEIGE_URL"):
        urls["feige"] = os.environ["DOUYIN_FEIGE_URL"]

    config["_root"] = ROOT_DIR
    config["_capture_dir"] = capture_dir
    config["_user_data_dir"] = user_data_dir
    return config
