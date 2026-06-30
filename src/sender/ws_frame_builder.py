from __future__ import annotations

import re
import time
import uuid
from typing import Any


def read_varint(data: bytes | bytearray, pos: int) -> tuple[int, int]:
    result = shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7f) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return result, pos


def write_varint(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def encode_bytes_field(field_number: int, value: bytes) -> bytes:
    tag = write_varint((field_number << 3) | 2)
    return tag + write_varint(len(value)) + value


class WSFrameBuilder:
    """Build fresh Feige WS text-message frames from a captured template."""

    CLIENT_MSG_ID = b"s:client_message_id"
    CHECK_SEND = b"p:check_Send"
    TYPE_TEXT_MARKER = b"type\x12\x04text"

    def __init__(self, template: bytes) -> None:
        self.template = bytes(template)

    @classmethod
    def from_template_dict(cls, template: dict[str, Any]) -> WSFrameBuilder:
        import base64

        payload = template.get("payload_template")
        if isinstance(payload, str) and payload:
            try:
                return cls(base64.b64decode(payload))
            except Exception:
                pass

        payload_hex = template.get("payload_hex", "")
        if payload_hex:
            return cls(bytes.fromhex(payload_hex))

        raise ValueError("template has no binary payload")

    def build_sync_frame(
        self,
        seq: int | None = None,
        timestamp_ms: int | None = None,
    ) -> bytes:
        """Build inbox follow-up frame (no text/signature fields)."""
        data = bytearray(self.template)
        now_ms = timestamp_ms or int(time.time() * 1000)
        old_top_seq, _ = read_varint(data, 1)
        new_seq = seq if seq is not None else old_top_seq + 1
        self._replace_send_time(data, str(now_ms))
        self._set_top_varint(data, 2, now_ms)
        self._replace_seq_values(data, old_top_seq, new_seq)
        return bytes(data)

    def build(
        self,
        text: str,
        seq: int | None = None,
        timestamp_ms: int | None = None,
    ) -> bytes:
        data = bytearray(self.template)
        now_ms = timestamp_ms or int(time.time() * 1000)

        old_top_seq, _ = read_varint(data, 1)
        new_seq = seq if seq is not None else old_top_seq + 1

        self._replace_text_content(data, text)
        self._replace_uuid_after(data, self.CHECK_SEND)
        self._replace_uuid_after(data, self.CLIENT_MSG_ID)
        self._replace_send_time(data, str(now_ms))
        self._replace_seq_values(data, old_top_seq, new_seq)
        self._set_top_varint(data, 2, now_ms)
        return bytes(data)

    def _replace_seq_values(self, data: bytearray, old_seq: int, new_seq: int) -> None:
        old_bytes = write_varint(old_seq)
        indices: list[int] = []
        pos = 0
        while True:
            idx = data.find(old_bytes, pos)
            if idx < 0:
                break
            if idx > 0 and data[idx - 1] in (0x08, 0x10):
                indices.append(idx)
            pos = idx + 1

        new_bytes = write_varint(new_seq)
        for idx in reversed(indices):
            self._replace_with_length_fixup(data, idx, idx + len(old_bytes), new_bytes)

    def _set_top_varint(self, data: bytearray, field_number: int, value: int) -> None:
        pos = 0
        while pos < min(len(data), 32):
            tag, next_pos = read_varint(data, pos)
            if (tag >> 3) == field_number and (tag & 7) == 0:
                _, value_end = read_varint(data, next_pos)
                replacement = write_varint(value)
                self._replace_with_length_fixup(data, next_pos, value_end, replacement)
                return
            if (tag & 7) == 0:
                _, pos = read_varint(data, next_pos)
            elif (tag & 7) == 2:
                length, length_end = read_varint(data, next_pos)
                pos = length_end + length
            else:
                break

    def _replace_text_content(self, data: bytearray, text: str) -> None:
        marker = self.TYPE_TEXT_MARKER
        idx = data.find(marker)
        if idx < 0:
            raise ValueError("text message marker not found in template")

        search_start = max(0, idx - 80)
        content_offset = self._find_next_utf8_content_field(data, search_start, idx + 20)
        if content_offset is None:
            raise ValueError("message content field not found in template")

        tag_pos = content_offset
        length, value_pos = read_varint(data, tag_pos + 1)
        old_start = value_pos
        old_end = old_start + length
        new_bytes = text.encode("utf-8")
        if len(new_bytes) <= 127:
            replacement = bytes([0x22]) + write_varint(len(new_bytes)) + new_bytes
        else:
            replacement = encode_bytes_field(4, new_bytes)

        self._replace_with_length_fixup(data, tag_pos, old_end, replacement)

    def _find_next_utf8_content_field(
        self,
        data: bytes | bytearray,
        start: int,
        end: int,
    ) -> int | None:
        pos = start
        while pos < min(len(data), end):
            if data[pos] != 0x22:
                pos += 1
                continue

            length, value_pos = read_varint(data, pos + 1)
            value_end = value_pos + length
            if value_end > len(data):
                return None

            chunk = data[value_pos:value_end]
            try:
                decoded = chunk.decode("utf-8")
            except UnicodeDecodeError:
                pos += 1
                continue

            if decoded and not decoded.startswith("{") and not decoded.startswith('"'):
                return pos
            pos += 1
        return None

    def _replace_with_length_fixup(
        self,
        data: bytearray,
        start: int,
        end: int,
        replacement: bytes,
    ) -> None:
        delta = len(replacement) - (end - start)
        data[start:end] = replacement

        if delta == 0:
            return

        parents = self._length_delimited_parents(data, start, end)
        for length_pos, length_size, old_length in reversed(parents):
            new_length = old_length + delta
            encoded = write_varint(new_length)
            if len(encoded) != length_size:
                raise ValueError("parent length varint size changed; template too complex")
            data[length_pos : length_pos + length_size] = encoded

    def _length_delimited_parents(
        self,
        data: bytes | bytearray,
        target_start: int,
        target_end: int,
    ) -> list[tuple[int, int, int]]:
        parents: list[tuple[int, int, int]] = []

        def walk(start: int, end: int) -> bool:
            pos = start
            while pos < end:
                tag, next_pos = read_varint(data, pos)
                wire = tag & 7
                if wire == 0:
                    _, pos = read_varint(data, next_pos)
                elif wire == 2:
                    length, length_pos = read_varint(data, next_pos)
                    length_size = length_pos - next_pos
                    chunk_start = length_pos
                    chunk_end = chunk_start + length
                    contains = chunk_start <= target_start and chunk_end >= target_end
                    if contains:
                        parents.append((next_pos, length_size, length))
                        if walk(chunk_start, chunk_end):
                            return True
                    pos = chunk_end
                elif wire == 1:
                    pos = next_pos + 8
                elif wire == 5:
                    pos = next_pos + 4
                else:
                    break
            return bool(parents)

        walk(0, len(data))
        return parents

    def _replace_uuid_after(self, data: bytearray, marker: bytes) -> None:
        start = 0
        while True:
            idx = data.find(marker, start)
            if idx < 0:
                return
            uuid_match = re.search(
                rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                data[idx : idx + 80],
            )
            if uuid_match:
                new_uuid = str(uuid.uuid4()).encode("ascii")
                abs_start = idx + uuid_match.start()
                data[abs_start : abs_start + 36] = new_uuid
            start = idx + 1

    def _replace_send_time(self, data: bytearray, send_time: str) -> None:
        marker = b'"send_time":"'
        idx = data.find(marker)
        if idx < 0:
            return
        value_start = idx + len(marker)
        value_end = data.find(b'"', value_start)
        if value_end < 0:
            return
        old = data[value_start:value_end]
        if len(send_time) != len(old):
            return
        data[value_start:value_end] = send_time.encode("ascii")
