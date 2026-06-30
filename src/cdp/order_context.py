from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from urllib.parse import parse_qs, urlencode, urlparse

from src.cdp.order_parse import build_order_summary, parse_componentized_orders, parse_response_text
from src.sender.frame_context import find_im_frame

logger = logging.getLogger("order_context")

_PAGE_FETCH_JS = (Path(__file__).parent / "page_fetch.js").read_text(encoding="utf-8")

ORDER_QUERY_PATH = "/backstage/cmpoent/order/query"
PRODUCT_CONSULT_PATH = "/backstage/workstation/get_consulting_products"
PRODUCT_LIST_PATH = "/backstage/workstation/get_product_list"

DOM_ORDER_SELECTORS = [
    '[class*="order-card"]',
    '[class*="shop-order"]',
    '[class*="order-item"]',
    '[class*="orderCard"]',
]
DOM_REFRESH_SELECTORS = [
    'button:has-text("刷新订单")',
    '[class*="refresh"]:has-text("刷新")',
    'span:has-text("刷新订单信息")',
]


def parse_security_user_id(conversation_id: str) -> str:
    cid = str(conversation_id or "").strip()
    if not cid:
        return ""
    head = cid.split(":")[0]
    if head.startswith("AQ") and len(head) > 20:
        return head
    return head if head.startswith("AQ") else ""


def parse_conversation_short_id(raw: str) -> str:
    s = str(raw or "").strip()
    if s.isdigit():
        return s
    return ""


def _empty_order_context(source: str = "none", summary: str = "当前买家暂无订单") -> dict[str, Any]:
    return {
        "has_order": False,
        "source": source,
        "orders": [],
        "latest_order": {},
        "summary": summary,
    }


def _order_context_from_orders(orders: list[dict[str, Any]], source: str) -> dict[str, Any]:
    if not orders:
        return _empty_order_context(source, "当前买家暂无订单")
    latest = orders[0]
    return {
        "has_order": True,
        "source": source,
        "orders": orders,
        "latest_order": latest,
        "summary": build_order_summary(orders),
    }


def _build_order_query_url(page_url: str = "") -> str:
    base = "https://pigeon.jinritemai.com/backstage/cmpoent/order/query"
    params = {
        "biz_type": "4",
        "PIGEON_BIZ_TYPE": "2",
        "_pms": "1",
        "device_platform": "web",
        "FUSION": "true",
        "_v": "1.0.1.7",
    }
    try:
        qs = parse_qs(urlparse(page_url or "").query)
        for key in ("verifyFp", "fp", "msToken", "a_bogus"):
            if qs.get(key):
                params[key] = qs[key][0]
    except Exception:
        pass
    return f"{base}?{urlencode(params)}"


async def _page_fetch(im_frame: Any, *, url: str, method: str = "GET", body: dict | None = None) -> dict[str, Any]:
    script = f"(async (payload) => {{ const run = {_PAGE_FETCH_JS}; return await run(payload); }})()"
    try:
        result = await asyncio.wait_for(
            im_frame.evaluate(script, {"url": url, "method": method, "body": body}),
            timeout=12.0,
        )
        return result if isinstance(result, dict) else {"ok": False, "status": 0, "text": ""}
    except asyncio.TimeoutError:
        return {"ok": False, "status": 0, "error": "timeout", "text": ""}
    except Exception as exc:
        return {"ok": False, "status": 0, "error": str(exc), "text": ""}


async def _read_session_ids(im_frame: Any) -> dict[str, str]:
    script = """
    () => {
      let store = null;
      window.__monaGlobalStore?.getData?.('initContextData')?.doAction?.((s) => { store = s; });
      const conv =
        store?.conversationsInfo?.currentConversation ||
        store?.sessionDetails?.currentConversation ||
        {};
      const convId = String(conv.id || conv.conversationId || '');
      const head = convId.split(':')[0] || '';
      const securityUserId =
        conv.securityUserId ||
        conv.security_user_id ||
        conv.userId ||
        conv.user_id ||
        (head.startsWith('AQ') ? head : '');
      const shortId = String(
        conv.talkId || conv.talk_id || conv.conversationShortId || conv.short_id || ''
      );
      return {
        conversation_id: convId,
        security_user_id: String(securityUserId || ''),
        conversation_short_id: shortId,
      };
    }
    """
    try:
        data = await im_frame.evaluate(script)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def fetch_orders_protocol(
    page: Any,
    *,
    security_user_id: str,
    conversation_short_id: str = "",
) -> dict[str, Any]:
    if not security_user_id:
        return _empty_order_context("protocol", "订单读取失败")

    im = await find_im_frame(page)
    page_url = str(getattr(page, "url", "") or "")
    url = _build_order_query_url(page_url)

    bodies = [
        {
            "security_user_id": security_user_id,
            "page_no": 0,
            "page_size": 5,
            "tab_type": 0,
            "search_words": "",
            "is_init_tab": 0,
            "biz_type": 2,
            "from_conversation_short_id": conversation_short_id,
            "version": "1.0",
            "open_params": {},
            "workstation_opt_version": "v2",
            "service_entity_id": "",
            "workstation_opt_gray": True,
        },
        {
            "security_user_id": security_user_id,
            "page_no": 0,
            "page_size": 5,
            "search_words": "",
            "is_init_tab": 1,
            "tab_type": 1,
            "biz_type": 2,
            "workstation_opt_version": "v2",
            "service_entity_id": "",
            "version": "1.0",
            "workstation_opt_gray": True,
        },
    ]

    for body in bodies:
        if not body.get("from_conversation_short_id"):
            body.pop("from_conversation_short_id", None)
        resp = await _page_fetch(im, url=url, method="POST", body=body)
        if not resp.get("ok") and not resp.get("text"):
            continue
        payload = parse_response_text(str(resp.get("text") or ""))
        if not payload or payload.get("code") not in (0, None):
            continue
        orders = parse_componentized_orders(payload)
        if orders:
            logger.info("order/query ok tab=%s count=%d", body.get("tab_type"), len(orders))
            return _order_context_from_orders(orders, "protocol")

    return _empty_order_context("protocol", "当前买家暂无订单")


async def read_orders_from_dom(page: Any) -> dict[str, Any]:
    script = """
    () => {
      const orders = [];
      const cards = document.querySelectorAll(
        '[class*="order-card"], [class*="shop-order"], [class*="orderCard"], [class*="order-item"]'
      );
      for (const card of cards) {
        const text = String(card.innerText || card.textContent || '').trim();
        if (!text || text.length < 8) continue;
        const idMatch = text.match(/\\d{15,20}/);
        const logistics = text.match(/([\\u4e00-\\u9fa5A-Za-z0-9]+快递)\\s+(\\d{10,22})/);
        const statusMatch = text.match(/(待发货|已发货|已完成|已签收|退款|售后|关闭)/);
        orders.push({
          order_id: idMatch ? idMatch[0] : '',
          status_desc: statusMatch ? statusMatch[1] : '',
          product_name: text.split('\\n')[0].slice(0, 80),
          express_company: logistics ? logistics[1] : '',
          tracking_no: logistics ? logistics[2] : '',
          logistics_status: /已签收/.test(text) ? '已签收' : '',
        });
      }
      const empty = /暂无订单|没有订单|无订单/.test(document.body.innerText || '');
      return { orders, empty };
    }
    """
    im = await find_im_frame(page)
    try:
        data = await im.evaluate(script)
    except Exception:
        return _empty_order_context("dom")
    if not isinstance(data, dict):
        return _empty_order_context("dom")
    orders = data.get("orders") or []
    if orders:
        return _order_context_from_orders(orders, "dom")
    if data.get("empty"):
        return _empty_order_context("dom", "当前买家暂无订单")
    return _empty_order_context("dom")


async def click_refresh_orders(page: Any) -> bool:
    im = await find_im_frame(page)
    for sel in DOM_REFRESH_SELECTORS:
        try:
            btn = im.locator(sel).first
            if await btn.count() > 0:
                await btn.click(timeout=2000)
                return True
        except Exception:
            continue
    return False


async def wait_order_panel(page: Any, timeout_ms: int = 5000) -> bool:
    im = await find_im_frame(page)
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    while asyncio.get_event_loop().time() < deadline:
        try:
            ok = await im.evaluate(
                """
                () => {
                  const t = document.body.innerText || '';
                  if (/暂无订单|没有订单|无订单/.test(t)) return true;
                  return document.querySelector('[class*="order-card"], [class*="shop-order"]') != null;
                }
                """
            )
            if ok:
                return True
        except Exception:
            pass
        await asyncio.sleep(0.3)
    return False


async def fetch_order_context(page: Any, conversation_id: str = "") -> dict[str, Any]:
    try:
        return await asyncio.wait_for(
            _fetch_order_context_inner(page, conversation_id),
            timeout=25.0,
        )
    except asyncio.TimeoutError:
        logger.warning("fetch_order_context timeout")
        return _empty_order_context("none", "订单读取失败")


async def _fetch_order_context_inner(page: Any, conversation_id: str = "") -> dict[str, Any]:
    im = await find_im_frame(page)
    session = await _read_session_ids(im)
    expected_uid = parse_security_user_id(conversation_id or session.get("conversation_id") or "")
    security_user_id = session.get("security_user_id") or expected_uid
    short_id = parse_conversation_short_id(session.get("conversation_short_id") or "")

    ctx = await fetch_orders_protocol(
        page,
        security_user_id=security_user_id,
        conversation_short_id=short_id,
    )
    if ctx.get("has_order"):
        ctx["security_user_id"] = security_user_id
        if expected_uid and security_user_id and expected_uid != security_user_id:
            return _empty_order_context("mismatch", "订单上下文与当前会话不匹配")
        return ctx

    dom_ctx = await read_orders_from_dom(page)
    dom_ctx["security_user_id"] = security_user_id
    if expected_uid and security_user_id and expected_uid != security_user_id:
        return _empty_order_context("mismatch", "订单上下文与当前会话不匹配")
    if dom_ctx.get("has_order") or dom_ctx.get("summary") == "当前买家暂无订单":
        return dom_ctx

    if await click_refresh_orders(page):
        await wait_order_panel(page, 5000)
        refreshed = await read_orders_from_dom(page)
        if refreshed.get("has_order") or refreshed.get("summary") == "当前买家暂无订单":
            refreshed["source"] = "refreshed_dom"
            return refreshed

    if ctx.get("source") == "protocol":
        return ctx
    return _empty_order_context("none", "订单读取失败")


async def fetch_consulting_products(page: Any, security_user_id: str) -> dict[str, Any]:
    if not security_user_id:
        return {"has_product": False, "products": [], "latest_product": {}, "source": "none"}
    im = await find_im_frame(page)
    url = f"https://pigeon.jinritemai.com{PRODUCT_CONSULT_PATH}?biz_type=4&PIGEON_BIZ_TYPE=2"
    resp = await _page_fetch(
        im,
        url=f"{url}&security_user_id={security_user_id}",
        method="GET",
    )
    payload = parse_response_text(str(resp.get("text") or ""))
    products = _extract_products(payload)
    if products:
        return {
            "has_product": True,
            "products": products,
            "latest_product": products[0],
            "source": "consulting_products",
        }
    return {"has_product": False, "products": [], "latest_product": {}, "source": "none"}


async def fetch_product_list(page: Any, security_user_id: str) -> dict[str, Any]:
    if not security_user_id:
        return {"has_product": False, "products": [], "latest_product": {}, "source": "none"}
    im = await find_im_frame(page)
    url = f"https://pigeon.jinritemai.com{PRODUCT_LIST_PATH}?biz_type=4&PIGEON_BIZ_TYPE=2"
    body = {
        "security_user_id": security_user_id,
        "presale_biz_scene": "b_user_footprint_consult",
        "page_size": 3,
        "service_entity_id": "",
    }
    resp = await _page_fetch(im, url=url, method="POST", body=body)
    payload = parse_response_text(str(resp.get("text") or ""))
    products = _extract_products(payload)
    if products:
        return {
            "has_product": True,
            "products": products,
            "latest_product": products[0],
            "source": "product_list",
        }
    return {"has_product": False, "products": [], "latest_product": {}, "source": "none"}


def _extract_products(payload: dict[str, Any] | None) -> list[dict[str, str]]:
    if not payload:
        return []
    products: list[dict[str, str]] = []
    nodes: list[Any] = []
    _walk_nodes(payload, nodes)
    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = str(
            node.get("product_name")
            or node.get("title")
            or node.get("name")
            or node.get("goods_name")
            or ""
        ).strip()
        if not name or len(name) < 2:
            continue
        products.append(
            {
                "product_id": str(node.get("product_id") or node.get("id") or ""),
                "product_name": name,
                "price_text": str(node.get("price_text") or node.get("price") or ""),
                "stock_text": str(node.get("stock_text") or node.get("stock") or ""),
                "status": str(node.get("status") or node.get("status_desc") or ""),
            }
        )
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for p in products:
        key = p["product_name"]
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out[:5]


def _walk_nodes(obj: Any, out: list[Any]) -> None:
    if isinstance(obj, dict):
        out.append(obj)
        for v in obj.values():
            _walk_nodes(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _walk_nodes(item, out)
