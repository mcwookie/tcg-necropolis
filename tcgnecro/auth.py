"""Sign-in and sessions.

Everything except /login, /logout and /static requires a signed-in profile.
The rest of the app only ever looks at `request.state.user` (a users.User, or
None on public pages), so the sign-in method can be replaced without touching
anything else.

Sign-in method today: a **profile picker**. You click your profile; there is
no password. That is fine on a home network you trust and NOT safe on the
internet -- anyone who can reach the page can pick any profile. The plan for
internet access (docs/ACCOUNTS.md) is to replace the picker with Google/GitHub
sign-in or a login proxy (Cloudflare Access); only the routes in this module
change when that happens.

Sessions are a signed cookie (Starlette's SessionMiddleware) holding the
profile id. The signing key is generated on first run into data/secret.key;
deleting that file signs everyone out.
"""

import secrets
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.middleware.sessions import SessionMiddleware

from tcgnecro import db, users
from tcgnecro.templating import templates

SESSION_COOKIE = "tcgnecro_session"
SESSION_DAYS = 30
PUBLIC_PATHS = ("/login", "/logout", "/static/")


def secret_key() -> str:
    path = db.data_dir() / "secret.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip()


class LazySessionMiddleware:
    """SessionMiddleware that reads its key from the data folder on first use
    rather than at import, so TCGNECRO_DATA can be set after the app module
    is imported (as the tests do)."""

    def __init__(self, app, https_only: bool = False):
        self.app = app
        self.https_only = https_only
        self._inner = None
        self._for: Path | None = None

    async def __call__(self, scope, receive, send):
        if self._inner is None or self._for != db.data_dir():
            self._for = db.data_dir()
            self._inner = SessionMiddleware(
                self.app, secret_key=secret_key(), session_cookie=SESSION_COOKIE,
                max_age=SESSION_DAYS * 24 * 3600, same_site="lax", https_only=self.https_only,
            )
        await self._inner(scope, receive, send)


class AuthMiddleware:
    """Puts the signed-in profile on request.state.user and sends everyone
    else to /login. Must sit inside (be added before) the session middleware."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        user = users.get(request.session.get("uid"))
        scope.setdefault("state", {})["user"] = user
        if user is None and not scope["path"].startswith(PUBLIC_PATHS):
            wanted = scope["path"] + ("?" + scope["query_string"].decode() if scope["query_string"] else "")
            target = "/login" + ("?" + urlencode({"next": wanted}) if wanted != "/" else "")
            if request.headers.get("hx-request"):
                # An HTMX fragment request: ask the page to navigate instead of
                # swapping the login page into part of it.
                response = Response(status_code=204, headers={"HX-Redirect": target})
            else:
                response = RedirectResponse(target, 303)
            return await response(scope, receive, send)
        await self.app(scope, receive, send)


def install(app, https_only: bool = False) -> None:
    # Starlette runs the last-added middleware first, so sessions are decoded
    # before AuthMiddleware looks at them.
    app.add_middleware(AuthMiddleware)
    app.add_middleware(LazySessionMiddleware, https_only=https_only)


def current_user(request: Request) -> users.User:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "Sign in first")
    return user


def _safe_next(next_url: str | None) -> str:
    """Only allow redirects back into this site."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"


router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", error: str | None = None):
    return templates.TemplateResponse(request, "login.html", {
        "profiles": users.list_users(), "next": _safe_next(next), "error": error,
    })


@router.post("/login")
def login(request: Request, user_id: str = Form(...), next: str = Form("/")):
    user = users.get(user_id)
    if user is None:
        return RedirectResponse("/login?error=Unknown+profile", 303)
    request.session["uid"] = user.id
    return RedirectResponse(_safe_next(next), 303)


@router.post("/login/setup")
def first_profile(request: Request, name: str = Form(...)):
    """Create the first (admin) profile. Only works while there are none."""
    if users.list_users():
        raise HTTPException(403, "Profiles already exist; ask an admin to add yours")
    try:
        user = users.create(name)
    except users.UserError as e:
        return RedirectResponse("/login?" + urlencode({"error": str(e)}), 303)
    request.session["uid"] = user.id
    return RedirectResponse("/", 303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", 303)


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, error: str | None = None, message: str | None = None):
    from tcgnecro.deckbuilder import store

    user = current_user(request)
    decks = store.list_decks()
    counts = {u.id: sum(1 for d in decks if d.owner == u.id) for u in users.list_users()}
    return templates.TemplateResponse(request, "profile.html", {
        "user": user, "profiles": users.list_users(), "deck_counts": counts,
        "error": error, "message": message,
    })


@router.post("/profile")
def update_profile(request: Request, name: str = Form(...), email: str = Form("")):
    user = current_user(request)
    try:
        users.update(user.id, name=name, email=email.strip() or None)
    except users.UserError as e:
        return RedirectResponse("/profile?" + urlencode({"error": str(e)}), 303)
    return RedirectResponse("/profile?message=Saved", 303)


@router.post("/profile/users")
def add_profile(request: Request, name: str = Form(...), is_admin: bool = Form(False)):
    """Admins add profiles for friends (they then pick it at /login)."""
    if not current_user(request).is_admin:
        raise HTTPException(403, "Only admins can add profiles")
    try:
        new = users.create(name, is_admin=is_admin)
    except users.UserError as e:
        return RedirectResponse("/profile?" + urlencode({"error": str(e)}), 303)
    return RedirectResponse("/profile?" + urlencode({"message": f"Added {new.name}"}), 303)
