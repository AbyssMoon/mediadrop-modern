from __future__ import annotations

import json
from base64 import b64decode, b64encode
from typing import Literal

import itsdangerous
from itsdangerous.exc import BadSignature
from starlette.datastructures import MutableHeaders, Secret
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth_config import DEFAULT_SESSION_TTL, load_auth_settings


class DynamicSessionMiddleware:
    """Signed cookie sessions whose Max-Age comes from the modern sidecar DB."""

    def __init__(
        self,
        app: ASGIApp,
        secret_key: str | Secret,
        session_cookie: str = "session",
        path: str = "/",
        same_site: Literal["lax", "strict", "none"] = "lax",
        https_only: bool = False,
        domain: str | None = None,
    ) -> None:
        self.app = app
        self.signer = itsdangerous.TimestampSigner(str(secret_key))
        self.session_cookie = session_cookie
        self.path = path
        self.security_flags = "httponly; samesite=" + same_site
        if https_only:
            self.security_flags += "; secure"
        if domain is not None:
            self.security_flags += f"; domain={domain}"

    @staticmethod
    def _max_age(scope: Scope) -> int:
        app = scope.get("app")
        if app is None or not hasattr(app.state, "modern_session_factory"):
            return DEFAULT_SESSION_TTL
        try:
            with app.state.modern_session_factory() as db:
                return load_auth_settings(db, app.state.settings).session_ttl_seconds
        except Exception:
            return DEFAULT_SESSION_TTL

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        connection = HTTPConnection(scope)
        initial_session_was_empty = True
        max_age = self._max_age(scope)

        if self.session_cookie in connection.cookies:
            data = connection.cookies[self.session_cookie].encode("utf-8")
            try:
                data = self.signer.unsign(data, max_age=max_age)
                scope["session"] = json.loads(b64decode(data))
                initial_session_was_empty = False
            except BadSignature:
                scope["session"] = {}
        else:
            scope["session"] = {}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                if scope["session"]:
                    data = b64encode(json.dumps(scope["session"]).encode("utf-8"))
                    data = self.signer.sign(data)
                    headers = MutableHeaders(scope=message)
                    header_value = (
                        f"{self.session_cookie}={data.decode('utf-8')}; path={self.path}; "
                        f"Max-Age={max_age}; {self.security_flags}"
                    )
                    headers.append("Set-Cookie", header_value)
                elif not initial_session_was_empty:
                    headers = MutableHeaders(scope=message)
                    header_value = (
                        f"{self.session_cookie}=null; path={self.path}; "
                        f"expires=Thu, 01 Jan 1970 00:00:00 GMT; {self.security_flags}"
                    )
                    headers.append("Set-Cookie", header_value)
            await send(message)

        await self.app(scope, receive, send_wrapper)
