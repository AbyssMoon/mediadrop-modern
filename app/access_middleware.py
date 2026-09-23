from __future__ import annotations

from urllib.parse import quote

from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth_config import load_auth_settings
from app.security import session_has_identity


class AuthenticationGateMiddleware:
    """Optionally require authentication before any public MediaDrop content."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @staticmethod
    def _is_exempt(path: str) -> bool:
        return (
            path == "/healthz"
            or path == "/login"
            or path == "/login/submit"
            or path == "/logout"
            or path.startswith("/static/")
            or path.startswith("/language/")
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        app = scope.get("app")
        if app is None:
            await self.app(scope, receive, send)
            return
        session = scope.get("session", {})
        try:
            with app.state.modern_session_factory() as db:
                auth = load_auth_settings(db, app.state.settings)
                if auth.require_login and session.get("user_id") and session.get("auth_backend") != "ldap":
                    # A disabled local account must not keep bypassing the global
                    # authentication gate with an already-issued session cookie.
                    from app.local_accounts import is_local_user_enabled

                    if not is_local_user_enabled(db, int(session["user_id"])):
                        session.clear()
        except Exception:
            auth = None
        if not auth or not auth.require_login:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "/")
        if self._is_exempt(path) or session_has_identity(session):
            await self.app(scope, receive, send)
            return

        if path.startswith("/api/"):
            response = JSONResponse({"detail": "Authentication required"}, status_code=401)
        else:
            query = scope.get("query_string", b"").decode("latin-1")
            destination = path + (f"?{query}" if query else "")
            response = RedirectResponse(f"/login?next={quote(destination, safe='')}", status_code=303)
        await response(scope, receive, send)
