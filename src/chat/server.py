from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.cdp.conversation_list import scan_conversation_list, switch_conversation
from src.cdp.current_conversation import read_current_conversation
from src.cdp.live_dom_probe import live_dom_probe
from src.cdp.page_session import PageSession
from src.chat.hub import ChatHub
from src.chat.send_service import send_text_message
from src.sender.api_sender import APISender
from src.sender.feige_navigator import FeigeNavigator
from src.sender.reply_sender import send_reply_async


STATIC_DIR = Path(__file__).resolve().parent / "static"


class SendRequest(BaseModel):
    text: str = Field(min_length=1)
    contact: str = Field(min_length=1)
    conversation_id: str | None = None
    conversation_key: str | None = None


class AssistantSendRequest(BaseModel):
    conversation_id: str = ""
    customer_hash: str = ""
    contact_name: str | None = None
    text: str = Field(min_length=1)
    mode: Literal["manual", "semi_auto", "auto"] = "semi_auto"


class ChatServerState:
    def __init__(
        self,
        hub: ChatHub,
        launcher: Any,
        schema_dir: Path,
        page_poller: Any | None = None,
    ) -> None:
        self.hub = hub
        self.launcher = launcher
        self.session = PageSession(launcher)
        self.schema_dir = schema_dir
        self.navigator = FeigeNavigator()
        self.sender = APISender(schema_dir)
        self.page_poller = page_poller
        self.dispatcher: Any | None = None


class SwitchConversationRequest(BaseModel):
    conversation_id: str = Field(min_length=1)
    customer_name: str = ""
    last_text: str = ""
    dom_row_index: int = -1


class ScanConversationsRequest(BaseModel):
    name_cache: dict[str, str] = Field(default_factory=dict)


class RefreshFeigeRequest(BaseModel):
    reason: Literal["interval", "stale_scan", "manual"] = "manual"
    name_cache: dict[str, str] = Field(default_factory=dict)
    check_idle: bool = True


def create_app(state: ChatServerState) -> FastAPI:
    app = FastAPI(title="Feige Chat UI")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/status")
    async def service_status() -> dict[str, Any]:
        from src.cdp.current_conversation import probe_page_state

        try:
            async def _probe(page: Any) -> dict[str, Any]:
                return await probe_page_state(page)

            probe, rebounded = await state.session.with_page_retry(_probe)
            return {
                "ok": True,
                "service": "doudian-cdp",
                "browser_connected": True,
                "page_bound": True,
                "PAGE_REBOUND": rebounded,
                **probe,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": True,
                "service": "doudian-cdp",
                "browser_connected": False,
                "page_bound": False,
                "logged_in": False,
                "PAGE_REBOUND": state.session.page_rebound,
                "message": str(exc),
            }

    @app.get("/api/debug/live-dom-probe")
    async def debug_live_dom_probe() -> dict[str, Any]:
        try:
            async def _probe(page: Any) -> dict[str, Any]:
                return await live_dom_probe(page)

            result, rebounded = await state.session.with_page_retry(_probe)
            if rebounded:
                result = {**result, "PAGE_REBOUND": True}
            return result
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": str(exc)}

    @app.get("/api/scan-conversations")
    async def scan_conversations_get() -> dict[str, Any]:
        return await _scan_conversations({})

    @app.post("/api/scan-conversations")
    async def scan_conversations_post(body: ScanConversationsRequest) -> dict[str, Any]:
        return await _scan_conversations(body.name_cache)

    async def _scan_conversations(name_cache: dict[str, str]) -> dict[str, Any]:
        try:
            async def _scan(page: Any) -> dict[str, Any]:
                return await scan_conversation_list(page, name_cache=name_cache)

            result, rebounded = await state.session.with_page_retry(_scan)
            if rebounded:
                result = {**result, "PAGE_REBOUND": True}
            return result
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": str(exc), "conversations": []}

    @app.post("/api/switch-conversation")
    async def switch_conversation_api(body: SwitchConversationRequest) -> dict[str, Any]:
        cid = body.conversation_id.strip()
        try:
            async def _switch(page: Any) -> dict[str, Any]:
                return await switch_conversation(
                    page,
                    cid,
                    customer_name=body.customer_name.strip(),
                    last_text=body.last_text.strip(),
                    dom_row_index=body.dom_row_index,
                )

            result, rebounded = await state.session.with_page_retry(_switch)
            if rebounded:
                result = {**result, "PAGE_REBOUND": True}
            return result
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": str(exc), "verified": False}

    @app.get("/api/dispatcher/status")
    async def dispatcher_status() -> dict[str, Any]:
        if not state.dispatcher:
            return {"ok": True, "running": False}
        return {"ok": True, **state.dispatcher.get_status()}

    @app.get("/api/current-conversation")
    async def current_conversation() -> dict[str, Any]:
        try:
            async def _read(page: Any) -> dict[str, Any]:
                return await read_current_conversation(page, state.hub)

            result, rebounded = await state.session.with_page_retry(_read)
            if rebounded:
                result = {**result, "PAGE_REBOUND": True}
            return result
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "connected": False,
                "error": "CDP_READ_ERROR",
                "message": str(exc),
            }

    @app.post("/api/refresh-feige")
    async def refresh_feige_api(body: RefreshFeigeRequest) -> dict[str, Any]:
        from src.cdp.feige_refresh import check_input_draft, refresh_feige_and_rescan

        try:
            async def _refresh(page: Any) -> dict[str, Any]:
                if body.check_idle:
                    if await check_input_draft(page):
                        return {
                            "ok": False,
                            "success": False,
                            "skipped": True,
                            "reason": "input_has_draft",
                            "message": "输入框有未发送内容，暂不刷新",
                        }
                return await refresh_feige_and_rescan(
                    page,
                    state.launcher,
                    name_cache=body.name_cache,
                )

            result, rebounded = await state.session.with_page_retry(_refresh)
            if rebounded and isinstance(result, dict):
                result = {**result, "PAGE_REBOUND": True}
            if isinstance(result, dict) and result.get("PAGE_REBOUND") and state.page_poller:
                page = await state.session.get_active_feige_page()
                await state.page_poller.rebind_page(page)
            if isinstance(result, dict) and result.get("success") and state.dispatcher:
                state.dispatcher.note_refresh()
            if isinstance(result, dict):
                result.setdefault("reason", body.reason)
            return result
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "success": False,
                "reason": body.reason,
                "error": str(exc),
                "message": "刷新飞鸽失败",
            }

    @app.get("/api/conversations")
    async def list_conversations() -> dict[str, Any]:
        return {"conversations": state.hub.list_conversations()}

    @app.get("/api/messages")
    async def list_messages(conversation_key: str | None = None) -> dict[str, Any]:
        return {
            "conversation_key": conversation_key,
            "messages": state.hub.list_messages(conversation_key),
        }

    @app.post("/api/assistant-send")
    async def assistant_send(body: AssistantSendRequest) -> dict[str, Any]:
        text = body.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="text is required")

        async def _send(page: Any) -> dict[str, Any]:
            return await send_reply_async(
                conversation_id=body.conversation_id,
                customer_hash=body.customer_hash,
                text=text,
                mode=body.mode,
                contact_name=body.contact_name,
                page=page,
            )

        result, _rebounded = await state.session.with_page_retry(_send)
        return result

    @app.post("/api/send")
    async def send_message(body: SendRequest) -> dict[str, Any]:
        text = body.text.strip()
        contact = body.contact.strip()
        if not text or not contact:
            raise HTTPException(status_code=400, detail="text and contact are required")

        try:
            async def _send(page: Any) -> dict[str, Any]:
                return await send_text_message(
                    page=page,
                    schema_dir=state.schema_dir,
                    hub=state.hub,
                    contact=contact,
                    text=text,
                    conversation_id=body.conversation_id,
                    conversation_key=body.conversation_key,
                    source="ui_send",
                )

            result, _rebounded = await state.session.with_page_retry(_send)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            if "Could not open chat" in str(exc):
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            raise

        return {
            "ok": True,
            "message": result["message"],
            "send_mode": result.get("send_mode"),
        }

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        state.hub.attach_client(websocket)
        try:
            await state.hub.send_snapshot(websocket)
            while True:
                raw = await websocket.receive_text()
                if raw == "ping":
                    await websocket.send_text('{"type":"pong"}')
        except WebSocketDisconnect:
            pass
        finally:
            state.hub.detach_client(websocket)

    return app
