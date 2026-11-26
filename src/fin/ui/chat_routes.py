"""The chat routes: the panel, sending a message, the event stream of a running turn, Stop, and
Allow once / Deny for a permission request. Each answer that changes the chat is the whole panel
again (`#chat`), built from the stored conversation, so a reload or a restart shows the same."""

import html
from collections.abc import Iterator
from pathlib import Path

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from fin.audit.errors import ChatBusy, FinError, UiActionInvalid
from fin.ui import chat_args, chat_store
from fin.ui.chat_run import Chat

SKILLS = ["ask", "ingest", "diligence", "report", "status"]
MAX_MESSAGE = 20000
KEEPALIVE_SECONDS = 15


def sse(name: str, data: str) -> str:
    """One server-sent event; every line of the data needs its own `data:` prefix."""
    body = "\n".join(f"data: {line}" for line in data.split("\n"))
    return f"event: {name}\n{body}\n\n"


def add_chat_routes(app: FastAPI, root: Path, templates: Jinja2Templates, chat: Chat) -> None:
    def panel(
        company: str,
        conv: chat_store.Conversation | None,
        prefill: str = "",
        notice: FinError | None = None,
    ) -> str:
        """The panel as HTML. Without a conversation it is a new chat."""
        missing = None
        try:
            chat.locate()
        except FinError as error:
            missing = error.message
        convs = chat_store.listing(root)
        shown = {
            "conv": conv,
            "convs": convs,
            "log": chat_store.items(conv) if conv else [],
            "running": bool(conv and chat.running(str(conv["id"]))),
            "prefill": prefill,
            "notice": {"code": notice.code, "message": notice.message} if notice else None,
            "api_key": chat_args.api_key_set(),
            "missing": missing,
            "skills": SKILLS,
        }
        return templates.get_template("_chat.html").render({"chat": shown, "company": company})

    def reply(*args: object, **kwargs: object) -> HTMLResponse:
        return HTMLResponse(panel(*args, **kwargs))  # type: ignore[arg-type]

    def current(conv_id: str, company: str) -> chat_store.Conversation:
        """The conversation, made new when no id is given."""
        return chat_store.load(root, conv_id) if conv_id else chat_store.create(root, company)

    @app.get("/chat/panel", response_class=HTMLResponse)
    def show(company: str = "", conv: str = "", new: str = "", prefill: str = "") -> HTMLResponse:
        if conv:
            chosen = chat_store.load(root, conv)
        else:
            found = [] if new else chat_store.listing(root)
            chosen = chat_store.load(root, str(found[0]["id"])) if found else None
        return reply(company, chosen, prefill)

    def begin(
        conv: chat_store.Conversation, company: str, text: str, once: str | None = None
    ) -> HTMLResponse:
        """Start the turn; the panel then shows the user's message and the live stream."""
        try:
            chat.start(conv, text, once)
        except ChatBusy as error:
            return reply(company, conv, notice=error)
        except FinError as error:  # claude is missing or cannot start: nothing was sent
            return reply(company, conv, text, error)
        return reply(company, conv)

    @app.post("/chat/send", response_class=HTMLResponse)
    def send(conv: str = Form(""), company: str = Form(""), text: str = Form("")) -> HTMLResponse:
        message = text.strip()
        if not message or len(message) > MAX_MESSAGE:
            raise UiActionInvalid(f"the message is empty or longer than {MAX_MESSAGE} characters")
        try:
            chat.locate()
        except FinError as error:  # E009: nothing is sent and no conversation is made
            return reply(company, chat_store.load(root, conv) if conv else None, text, error)
        chosen = current(conv, company)
        if chat.running(str(chosen["id"])):
            busy = ChatBusy("this conversation is still working: wait for it or press Stop")
            return reply(company, chosen, text, busy)
        chat_store.add_user_item(root, chosen, message)
        return begin(chosen, company, message)

    def permission(conv: chat_store.Conversation, item_id: str) -> dict[str, object]:
        for found in chat_store.items(conv):
            if found.get("kind") == "permission" and found.get("id") == item_id:
                if found.get("state") != "pending":
                    raise UiActionInvalid("that request was already answered")
                return found
        raise UiActionInvalid(f"unknown permission request {item_id!r}")

    @app.post("/chat/allow", response_class=HTMLResponse)
    def allow(conv: str = Form(), item: str = Form()) -> HTMLResponse:
        chosen = chat_store.load(root, conv)
        found = permission(chosen, item)
        company = str(chosen["company"])
        rule = chat_args.checked_rule(str(found["rule"]))
        if chat.running(conv):
            return reply(company, chosen, notice=ChatBusy("this conversation is still working"))
        found["state"] = "allowed"
        said = f"Allowed once: {found['tool']} {found['summary']}. Please go on."
        chat_store.add_user_item(root, chosen, said)
        return begin(chosen, company, said, rule)

    @app.post("/chat/deny", response_class=HTMLResponse)
    def deny(conv: str = Form(), item: str = Form()) -> HTMLResponse:
        chosen = chat_store.load(root, conv)
        permission(chosen, item)["state"] = "denied"
        chat_store.save(root, chosen)
        return reply(str(chosen["company"]), chosen)

    @app.post("/chat/stop")
    def stop(conv: str = Form()) -> Response:
        chat.stop(str(chat_store.load(root, conv)["id"]))
        return Response(status_code=204)

    @app.get("/chat/stream")
    def stream(request: Request, conv: str) -> StreamingResponse:
        chosen = chat_store.load(root, conv)
        item_macro = templates.get_template("_chat_items.html").module.item  # type: ignore[attr-defined]

        def events() -> Iterator[str]:
            turn = chat.turns.get(conv)
            if turn is None:  # nothing is running (the server restarted): show the stored chat
                yield sse("done", panel(str(chosen["company"]), chosen))
                return
            sent = 0
            while True:
                with turn.cond:
                    if sent >= len(turn.events) and not turn.finished:
                        turn.cond.wait(timeout=KEEPALIVE_SECONDS)
                    batch = turn.events[sent:]
                    sent += len(batch)
                if not batch:
                    yield ": keepalive\n\n"
                for event in batch:
                    if event["name"] == "block":
                        yield sse("block", str(item_macro(event["item"], conv)))
                    elif event["name"] == "delta":
                        yield sse("delta", html.escape(str(event["text"]), quote=False))
                    else:  # done: the stored conversation is complete, show it
                        fresh = chat_store.load(root, conv)
                        yield sse("done", panel(str(fresh["company"]), fresh))
                        return

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )
