"""The chat routes: the panel, sending a message, the event stream of a running turn, Stop, and
Allow once / Deny for a permission request. Each answer that changes the chat is the whole panel
again (`#chat`), built from the stored conversation, so a reload or a restart shows the same."""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from fin.audit.errors import ChatBusy, FinError, UiActionInvalid
from fin.output import escape
from fin.ui import chat_args, chat_store
from fin.ui.chat_run import Chat

SKILLS = ["ask", "ingest", "diligence", "report", "status"]
MAX_MESSAGE = 20000
KEEPALIVE_SECONDS = 15


def sse(name: str, data: str) -> str:
    """One server-sent event; every line of the data needs its own `data:` prefix."""
    body = "\n".join(f"data: {line}" for line in data.split("\n"))
    return f"event: {name}\n{body}\n\n"


def _batches(turn: Any) -> Iterator[list[dict[str, Any]]]:
    """The events of a running turn as they come, in batches; an empty batch after
    KEEPALIVE_SECONDS without one. Ends after the batch that holds the last event."""
    sent = 0
    while True:
        with turn.cond:
            if sent >= len(turn.events) and not turn.finished:
                turn.cond.wait(timeout=KEEPALIVE_SECONDS)
            batch = turn.events[sent:]
            finished = turn.finished
            sent += len(batch)
        yield batch
        if finished and sent >= len(turn.events):
            return


class ChatRoutes:
    """The route handlers, over the data folder, the templates and the running chat."""

    def __init__(self, root: Path, templates: Jinja2Templates, chat: Chat) -> None:
        self.root, self.templates, self.chat = root, templates, chat

    def panel(
        self,
        company: str,
        conv: chat_store.Conversation | None,
        prefill: str = "",
        notice: FinError | None = None,
    ) -> str:
        """The panel as HTML. Without a conversation it is a new chat."""
        missing = None
        try:
            self.chat.locate()
        except FinError as error:
            missing = error.message
        shown = {
            "conv": conv,
            "convs": chat_store.listing(self.root),
            "log": chat_store.items(conv) if conv else [],
            "running": bool(conv and self.chat.running(str(conv["id"]))),
            "prefill": prefill,
            "notice": {"code": notice.code, "message": notice.message} if notice else None,
            "api_key": chat_args.api_key_set(),
            "missing": missing,
            "skills": SKILLS,
        }
        page = self.templates.get_template("_chat.html")
        return page.render({"chat": shown, "company": company})

    def reply(
        self,
        company: str,
        conv: chat_store.Conversation | None,
        prefill: str = "",
        notice: FinError | None = None,
    ) -> HTMLResponse:
        return HTMLResponse(self.panel(company, conv, prefill, notice))

    def show(
        self, company: str = "", conv: str = "", new: str = "", prefill: str = ""
    ) -> HTMLResponse:
        if conv:
            return self.reply(company, chat_store.load(self.root, conv), prefill)
        found = [] if new else chat_store.listing(self.root)
        chosen = chat_store.load(self.root, str(found[0]["id"])) if found else None
        return self.reply(company, chosen, prefill)

    def begin(
        self, conv: chat_store.Conversation, company: str, text: str, once: str | None = None
    ) -> HTMLResponse:
        """Start the turn; the panel then shows the user's message and the live stream."""
        try:
            self.chat.start(conv, text, once)
        except ChatBusy as error:
            return self.reply(company, conv, notice=error)
        except FinError as error:  # claude is missing or cannot start: nothing was sent
            return self.reply(company, conv, text, error)
        return self.reply(company, conv)

    def send(
        self, conv: str = Form(""), company: str = Form(""), text: str = Form("")
    ) -> HTMLResponse:
        message = text.strip()
        if not message or len(message) > MAX_MESSAGE:
            raise UiActionInvalid(f"the message is empty or longer than {MAX_MESSAGE} characters")
        try:
            self.chat.locate()
        except FinError as error:  # E009: nothing is sent and no conversation is made
            known = chat_store.load(self.root, conv) if conv else None
            return self.reply(company, known, text, error)
        # the conversation named, or a new one
        chosen = chat_store.load(self.root, conv) if conv else chat_store.create(self.root, company)
        if self.chat.running(str(chosen["id"])):
            busy = ChatBusy("this conversation is still working: wait for it or press Stop")
            return self.reply(company, chosen, text, busy)
        chat_store.add_user_item(self.root, chosen, message)
        return self.begin(chosen, company, message)

    def allow(self, conv: str = Form(), item: str = Form()) -> HTMLResponse:
        chosen = chat_store.load(self.root, conv)
        found = _permission(chosen, item)
        company = str(chosen["company"])
        rule = chat_args.checked_rule(str(found["rule"]))
        if self.chat.running(conv):
            return self.reply(
                company, chosen, notice=ChatBusy("this conversation is still working")
            )
        found["state"] = "allowed"
        said = f"Allowed once: {found['tool']} {found['summary']}. Please go on."
        chat_store.add_user_item(self.root, chosen, said)
        return self.begin(chosen, company, said, rule)

    def deny(self, conv: str = Form(), item: str = Form()) -> HTMLResponse:
        chosen = chat_store.load(self.root, conv)
        _permission(chosen, item)["state"] = "denied"
        chat_store.save(self.root, chosen)
        return self.reply(str(chosen["company"]), chosen)

    def stop(self, conv: str = Form()) -> Response:
        self.chat.stop(str(chat_store.load(self.root, conv)["id"]))
        return Response(status_code=204)

    def stream(self, request: Request, conv: str) -> StreamingResponse:
        chosen = chat_store.load(self.root, conv)
        return StreamingResponse(
            self._events(conv, chosen),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    def _events(self, conv: str, chosen: chat_store.Conversation) -> Iterator[str]:
        turn = self.chat.turns.get(conv)
        if turn is None:  # nothing is running (the server restarted): show the stored chat
            yield sse("done", self.panel(str(chosen["company"]), chosen))
            return
        item = self.templates.get_template("_chat_items.html").module.item  # type: ignore[attr-defined]
        for batch in _batches(turn):
            if not batch:
                yield ": keepalive\n\n"
            for event in batch:
                yield self._event(event, conv, item)
                if event["name"] not in ("block", "delta"):
                    return

    def _event(self, event: dict[str, Any], conv: str, item: Callable[..., Any]) -> str:
        """One event of the turn as SSE: a finished block, a piece of text, or done (the stored
        conversation is complete: show it)."""
        if event["name"] == "block":
            return sse("block", str(item(event["item"], conv)))
        if event["name"] == "delta":
            return sse("delta", escape.html(event["text"]))
        fresh = chat_store.load(self.root, conv)
        return sse("done", self.panel(str(fresh["company"]), fresh))


def _permission(conv: chat_store.Conversation, item_id: str) -> dict[str, object]:
    for found in chat_store.items(conv):
        if found.get("kind") == "permission" and found.get("id") == item_id:
            if found.get("state") != "pending":
                raise UiActionInvalid("that request was already answered")
            return found
    raise UiActionInvalid(f"unknown permission request {item_id!r}")


def add_chat_routes(app: FastAPI, root: Path, templates: Jinja2Templates, chat: Chat) -> None:
    routes = ChatRoutes(root, templates, chat)
    app.get("/chat/panel", response_class=HTMLResponse)(routes.show)
    app.post("/chat/send", response_class=HTMLResponse)(routes.send)
    app.post("/chat/allow", response_class=HTMLResponse)(routes.allow)
    app.post("/chat/deny", response_class=HTMLResponse)(routes.deny)
    app.post("/chat/stop")(routes.stop)
    app.get("/chat/stream")(routes.stream)
