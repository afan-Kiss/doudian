from __future__ import annotations

import base64
import json
import re
from typing import Any

LOGISTICS_LINE_RE = re.compile(
    r"(?P<company>[\u4e00-\u9fa5A-Za-z0-9]+快递)\s+(?P<tracking>\d{10,22})"
)
LOGISTICS_STATUS_RE = re.compile(
    r"\[(?P<status>[^\]]+)\]\s*(?P<time>[\d\- :]+)?"
)
ORDER_ID_FROM_KEY_RE = re.compile(r"shop_order_(\d{15,20})")


def parse_response_text(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    try:
        decoded = base64.b64decode(raw, validate=False).decode("utf-8", errors="ignore")
        return json.loads(decoded)
    except Exception:
        return None


def _walk(obj: Any, out: list[Any]) -> None:
    if isinstance(obj, dict):
        out.append(obj)
        for v in obj.values():
            _walk(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _walk(item, out)


def _parse_logistics_text(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if not text:
        return result
    m = LOGISTICS_LINE_RE.search(text.replace("\n", " "))
    if m:
        result["express_company"] = m.group("company").strip()
        result["tracking_no"] = m.group("tracking").strip()
    sm = LOGISTICS_STATUS_RE.search(text)
    if sm:
        result["logistics_status"] = sm.group("status").strip()
        if sm.group("time"):
            result["tracking_time"] = sm.group("time").strip()
    return result


def _merge_logistics(order: dict[str, Any], fields: dict[str, Any]) -> None:
    for key in (
        "logistics_text",
        "logistics_text_v2",
        "logistics_sub_text",
        "logistics_sub_text_v2",
    ):
        val = fields.get(key)
        if isinstance(val, str) and val.strip():
            parsed = _parse_logistics_text(val)
            for k, v in parsed.items():
                if v and not order.get(k):
                    order[k] = v
    btn = fields.get("copy_button")
    if isinstance(btn, dict):
        ext = btn.get("button_ext") or {}
        txt = str(ext.get("text") or "").strip()
        if txt and not order.get("tracking_no"):
            parsed = _parse_logistics_text(txt)
            order.update({k: v for k, v in parsed.items() if v})
    info = fields.get("logistics_info")
    if isinstance(info, dict):
        for k, dst in (
            ("express_company", "express_company"),
            ("tracking_no", "tracking_no"),
            ("tracking_time", "tracking_time"),
            ("state_desc", "logistics_status"),
        ):
            v = str(info.get(k) or "").strip()
            if v and not order.get(dst):
                order[dst] = v


def parse_componentized_orders(payload: dict[str, Any]) -> list[dict[str, Any]]:
    comp = payload.get("componentized_data") or {}
    data = comp.get("data") or {}
    hierarchy = (comp.get("hierarchy") or {}).get("structure") or {}
    orders_by_id: dict[str, dict[str, Any]] = {}

    root_keys = hierarchy.get(comp.get("hierarchy", {}).get("root", "root_1"), [])
    if not root_keys:
        for key in data:
            m = ORDER_ID_FROM_KEY_RE.search(key)
            if m:
                root_keys.append(f"shop_order_{m.group(1)}")

    for root_key in root_keys:
        m = ORDER_ID_FROM_KEY_RE.search(root_key)
        if not m:
            continue
        oid = m.group(1)
        orders_by_id.setdefault(
            oid,
            {
                "order_id": oid,
                "status": "",
                "status_desc": "",
                "product_name": "",
                "amount_text": "",
                "express_company": "",
                "tracking_no": "",
                "logistics_status": "",
                "tracking_time": "",
                "aftersale_status": "",
            },
        )

    for key, node in data.items():
        if not isinstance(node, dict):
            continue
        fields = node.get("fields") or {}
        m = ORDER_ID_FROM_KEY_RE.search(key)
        if not m:
            continue
        oid = m.group(1)
        order = orders_by_id.setdefault(
            oid,
            {
                "order_id": oid,
                "status": "",
                "status_desc": "",
                "product_name": "",
                "amount_text": "",
                "express_company": "",
                "tracking_no": "",
                "logistics_status": "",
                "tracking_time": "",
                "aftersale_status": "",
            },
        )

        if "order_status_desc" in fields and fields["order_status_desc"]:
            order["status_desc"] = str(fields["order_status_desc"]).strip()
        if "order_id" in fields and fields["order_id"]:
            order["order_id"] = str(fields["order_id"]).strip()

        ext = fields.get("ext") or {}
        if isinstance(ext, dict):
            amt = ext.get("actual_pay_amount_str") or ext.get("actual_pay_amount")
            if amt and not order.get("amount_text"):
                order["amount_text"] = str(amt).strip()
            sku_list = ext.get("sku_order_list") or []
            if isinstance(sku_list, list):
                names = [
                    str(s.get("product_name") or "").strip()
                    for s in sku_list
                    if isinstance(s, dict) and s.get("product_name")
                ]
                if names and not order.get("product_name"):
                    order["product_name"] = "、".join(names[:3])

        if fields.get("aftersale_sum_status_desc"):
            order["aftersale_status"] = str(fields["aftersale_sum_status_desc"]).strip()

        _merge_logistics(order, fields)

        for sub_key, sub_val in fields.items():
            if isinstance(sub_val, str) and "快递" in sub_val:
                _merge_logistics(order, {sub_key: sub_val})

    return list(orders_by_id.values())


def build_order_summary(orders: list[dict[str, Any]]) -> str:
    if not orders:
        return "当前买家暂无订单"
    latest = orders[0]
    parts = []
    if latest.get("status_desc"):
        parts.append(str(latest["status_desc"]))
    if latest.get("product_name"):
        parts.append(str(latest["product_name"]))
    if latest.get("express_company"):
        parts.append(str(latest["express_company"]))
    if latest.get("logistics_status"):
        parts.append(str(latest["logistics_status"]))
    return "；".join(parts) if parts else "有订单"
