from __future__ import annotations

import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def browser_executable(config: dict[str, Any]) -> str:
    return str(
        os.environ.get("BROWSER_EXECUTABLE_PATH")
        or config.get("browser", {}).get("executable_path")
        or r"C:\Users\1\AppData\Local\Google\Chrome\Application\chrome.exe"
    )


def cdp_port(config: dict[str, Any]) -> int:
    raw = os.environ.get("CDP_PORT") or config.get("browser", {}).get("debug_port") or 9222
    return int(raw)


def chrome_profile_dir(config: dict[str, Any]) -> Path:
    raw = (
        os.environ.get("DOUYIN_CDP_PROFILE")
        or config.get("browser", {}).get("user_data_dir")
        or r"D:\douyin-customer-assistant\data\chrome-profile"
    )
    path = Path(raw)
    if not path.is_absolute():
        path = Path(config["_root"]) / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def feige_url(config: dict[str, Any]) -> str:
    return str(
        os.environ.get("DOUYIN_FEIGE_URL")
        or config.get("urls", {}).get("feige")
        or "https://im.jinritemai.com/pc_seller_v2/main"
    )


def cdp_endpoint(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def is_cdp_ready(port: int, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{cdp_endpoint(port)}/json/version", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def wait_for_cdp(port: int, timeout_sec: float = 90.0) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if is_cdp_ready(port):
            return True
        time.sleep(0.5)
    return False


def _port_in_use(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def resolve_cdp_port(config: dict[str, Any]) -> int:
    preferred = cdp_port(config)
    if is_cdp_ready(preferred):
        return preferred
    if not _port_in_use(preferred):
        return preferred
    for alt in (9223, 9224, 9225):
        if not _port_in_use(alt) or is_cdp_ready(alt):
            os.environ["CDP_PORT"] = str(alt)
            config.setdefault("browser", {})["debug_port"] = alt
            return alt
    return preferred


def launch_chrome(config: dict[str, Any], port: int | None = None) -> subprocess.Popen[Any] | None:
    port = port or resolve_cdp_port(config)
    exe = browser_executable(config)
    if not Path(exe).exists():
        raise FileNotFoundError(f"Chrome not found: {exe}")

    profile = chrome_profile_dir(config)
    url = feige_url(config)
    args = [
        exe,
        f"--remote-debugging-port={port}",
        f'--user-data-dir={profile}',
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ensure_chrome_cdp(config: dict[str, Any], wait_sec: float = 90.0) -> int:
    port = resolve_cdp_port(config)
    if is_cdp_ready(port):
        return port

    if _port_in_use(port) and not is_cdp_ready(port):
        print(f"[cdp] 端口 {port} 被占用且非 Chrome CDP，尝试备用端口…")
        port = resolve_cdp_port(config)

    if not is_cdp_ready(port):
        print(f"[cdp] 启动本机 Chrome CDP 端口 {port} …")
        launch_chrome(config, port)
        if not wait_for_cdp(port, wait_sec):
            raise RuntimeError(f"Chrome CDP 未在 {wait_sec}s 内就绪 (port={port})")
    return port
