from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SchemaDumper:
    """Dump analyzed message structures to schema files."""

    def __init__(self, capture_dir: Path) -> None:
        self.capture_dir = capture_dir
        self.schema_dir = capture_dir / "schema"
        self.schema_dir.mkdir(parents=True, exist_ok=True)

    def dump(self, analysis: dict[str, Any]) -> dict[str, Path]:
        message_types = {}
        for msg_type, samples in analysis.get("message_types", {}).items():
            message_types[msg_type] = {
                "count": len(samples),
                "samples": samples[:3],
            }

        send_templates = analysis.get("send_templates", [])
        ws_endpoints = analysis.get("ws_endpoints", [])
        latest = self._pick_best_template(send_templates)

        outputs = {
            "message_types.json": self.schema_dir / "message_types.json",
            "send_template.json": self.schema_dir / "send_template.json",
            "ws_endpoints.json": self.schema_dir / "ws_endpoints.json",
            "parsed_messages.json": self.schema_dir / "parsed_messages.json",
        }

        self._write(outputs["message_types.json"], message_types)
        self._write(
            outputs["send_template.json"],
            {
                "count": len(send_templates),
                "templates": send_templates[:20],
                "latest": latest,
            },
        )
        self._write(outputs["ws_endpoints.json"], {"endpoints": ws_endpoints})
        self._write(
            outputs["parsed_messages.json"],
            {
                "count": len(analysis.get("parsed_messages", [])),
                "messages": analysis.get("parsed_messages", [])[-100:],
            },
        )

        return outputs

    def _pick_best_template(self, send_templates: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not send_templates:
            return None

        message_frames = [t for t in send_templates if t.get("is_message_send")]
        if message_frames:
            return message_frames[-1]

        ws_frames = [t for t in send_templates if t.get("transport") == "websocket"]
        if ws_frames:
            return min(ws_frames, key=lambda item: item.get("payload_length", 10**9))

        return send_templates[-1]

    def _write(self, path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
