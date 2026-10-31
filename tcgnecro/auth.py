"""Sign-in and sessions.

Everything except /login, /logout, /static and /healthz requires a signed-in profile.
The rest of the app only ever looks at `request.state.user` (a users.User, or
None on public pages), so the sign-in method can be replaced without touching
anything else.

Two sign-in methods:

- **Profile picker** (the default). You click your profile; there is no
  password. Fine on a home network you trust, NOT safe on the internet:
  anyone who can reach the page can pick any profile.
- **Cloudflare Access** (set TCGNECRO_CF_ACCESS_TEAM and
  TCGNECRO_CF_ACCESS_AUD). Cloudflare signs people in (Google, GitHub, an
  emailed code...) before they reach the app, and sends a signed token with
  every request. The app checks that token's signature, audience and expiry,
  and signs the person in as the profile with the token's email; there is no
  picker. A request without a valid token is refused, so the app can't be
  reached around Cloudflare (e.g. on its LAN port). See docs/ACCOUNTS.md.

Sessions are a signed cookie (Starlette's SessionMiddleware) holding the
profile id. The signing key is generated on first run into data/secret.key;
deleting that file signs everyone out.
"""

import logging
import os
import secrets
from functools import lru_cache
from pathlib import Path
from zoneinfo import available_timezones
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from tcgnecro import db, users
from tcgnecro.templating import templates

log = logging.getLogger("tcgnecro.auth")

SESSION_COOKIE = "tcgnecro_session"
SESSION_DAYS = 30
PUBLIC_PATHS = ("/login", "/logout", "/static/", "/healthz")


# ----- Cloudflare Access --------------------------------------------------------

class CloudflareAccess:
    """Checks the token Cloudflare Access attaches to each request it lets
    through (the Cf-Access-Jwt-Assertion header, or the CF_Authorization
    cookie): signed with the team's keys, for this application (audience),
    from this team (issuer), and not expired."""

    def __init__(self, team: str, audience: str):
        team = team.strip().removeprefix("https://").rstrip("/")
        domain = team if "." in team else f"{team}.cloudflareaccess.com"
        self.issuer = f"https://{domain}"
        self.audience = audience.strip()
        import jwt
        self._jwks = jwt.PyJWKClient(f"{self.issuer}/cdn-cgi/access/certs", cache_keys=True, lifespan=3600,
                                     headers={"User-Agent": "tcgnecro (+https://github.com/mcwookie/tcg-necropolis)"},
                                     timeout=10)

    def signing_key(self, token: str):
        return self._jwks.get_signing_key_from_jwt(token).key

    def email(self, request: Request) -> str | None:
        """The verified email, or None without a valid token."""
        import jwt
        token = request.headers.get("cf-access-jwt-assertion") or request.cookies.get("CF_Authorization")
        if not token:
            return None
        try:
            claims = jwt.decode(token, self.signing_key(token), algorithms=["RS256"], audience=self.audience,
                                issuer=self.issuer, leeway=60, options={"require": ["exp", "aud", "iss"]})
        except jwt.PyJWTError as e:
            log.warning("Cloudflare Access token refused: %s", e)
            return None
        return users.normalize_email(claims.get("email"))


@lru_cache(maxsize=4)
def _cloudflare(team: str, audience: str) -> CloudflareAccess:
    return CloudflareAccess(team, audience)


def cloudflare() -> CloudflareAccess | None:
    """The Cloudflare Access checker when it's configured, else None (the
    profile picker). Read per request, so settings can change in tests."""
    team, audience = os.environ.get("TCGNECRO_CF_ACCESS_TEAM", ""), os.environ.get("TCGNECRO_CF_ACCESS_AUD", "")
    return _cloudflare(team, audience) if team.strip() and audience.strip() else None


NOT_THROUGH_CLOUDFLARE = """<!doctype html><meta charset="utf-8"><title>Sign in through Cloudflare</title>
<body style="font: 16px system-ui; max-width: 36em; margin: 3em auto; padding: 0 1em">
<h1>Sign in through Cloudflare</h1>
<p>This TCG Necropolis only accepts people signed in through Cloudflare Access. Open it at
its internet address (not its local network address), and sign in there.</p></body>"""


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
        state = scope.setdefault("state", {})
        cf = cloudflare()
        state["cloudflare"] = cf is not None
        if cf is not None:
            return await self._cloudflare(cf, request, scope, receive, send)
        user = users.get(request.session.get("uid"))
        state["user"] = user
        if user is None and not scope["path"].startswith(PUBLIC_PATHS):
            return await self._to_login(request, scope, receive, send)
        await self.app(scope, receive, send)

    async def _cloudflare(self, cf, request, scope, receive, send):
        """Signed in as the profile with the verified email. No token: refused.
        A verified email with no profile: /login explains (or, with no profiles
        yet, sets up the admin)."""
        path = scope["path"]
        if path.startswith("/static/") or path == "/healthz":   # the container health check
            return await self.app(scope, receive, send)
        email = await run_in_threadpool(cf.email, request)   # may fetch Cloudflare's keys
        if email is None:
            return await HTMLResponse(NOT_THROUGH_CLOUDFLARE, 403)(scope, receive, send)
        user = users.by_email(email)
        scope["state"]["user"] = user
        scope["state"]["cf_email"] = email
        if user is None and path not in ("/login", "/login/setup"):
            return await self._to_login(request, scope, receive, send)
        await self.app(scope, receive, send)

    async def _to_login(self, request, scope, receive, send):
        wanted = scope["path"] + ("?" + scope["query_string"].decode() if scope["query_string"] else "")
        target = "/login" + ("?" + urlencode({"next": wanted}) if wanted != "/" else "")
        if request.headers.get("hx-request"):
            # An HTMX fragment request: ask the page to navigate instead of
            # swapping the login page into part of it.
            response = Response(status_code=204, headers={"HX-Redirect": target})
        else:
            response = RedirectResponse(target, 303)
        return await response(scope, receive, send)


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


def _cloudflare_mode(request: Request) -> bool:
    return bool(getattr(request.state, "cloudflare", False))


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", error: str | None = None):
    if _cloudflare_mode(request) and getattr(request.state, "user", None):
        return RedirectResponse(_safe_next(next), 303)      # already signed in by Cloudflare
    return templates.TemplateResponse(request, "login.html", {
        "profiles": users.list_users(), "next": _safe_next(next), "error": error,
        "cloudflare": _cloudflare_mode(request), "cf_email": getattr(request.state, "cf_email", None),
    })


@router.post("/login")
def login(request: Request, user_id: str = Form(...), next: str = Form("/")):
    if _cloudflare_mode(request):
        raise HTTPException(403, "Signing in is done by Cloudflare Access here")
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
        # With Cloudflare Access, the admin profile gets the email they signed in with.
        user = users.create(name, email=getattr(request.state, "cf_email", None))
    except users.UserError as e:
        return RedirectResponse("/login?" + urlencode({"error": str(e)}), 303)
    request.session["uid"] = user.id
    return RedirectResponse("/", 303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    if _cloudflare_mode(request):
        # Cloudflare's own sign-out for this site, which ends its session.
        return RedirectResponse("/cdn-cgi/access/logout", 303)
    return RedirectResponse("/login", 303)


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, error: str | None = None, message: str | None = None):
    from tcgnecro.deckbuilder import store

    user = current_user(request)
    decks = store.list_decks()
    counts = {u.id: sum(1 for d in decks if d.owner == u.id) for u in users.list_users()}
    return templates.TemplateResponse(request, "profile.html", {
        "user": user, "profiles": users.list_users(), "deck_counts": counts,
        "timezones": sorted(available_timezones()),
        "error": error, "message": message, "cloudflare": _cloudflare_mode(request),
    })


@router.post("/profile")
def update_profile(request: Request, name: str = Form(...), email: str = Form(""), timezone: str = Form("")):
    user = current_user(request)
    if _cloudflare_mode(request):
        email = user.email or ""   # it's how Cloudflare signs you in; an admin changes it
    if timezone and timezone not in available_timezones():
        return RedirectResponse("/profile?" + urlencode({"error": f"Unknown time zone '{timezone}'"}), 303)
    prefs = {k: v for k, v in user.prefs.items() if k != "timezone"} | ({"timezone": timezone} if timezone else {})
    try:
        users.update(user.id, name=name, email=email.strip() or None, prefs=prefs)
    except users.UserError as e:
        return RedirectResponse("/profile?" + urlencode({"error": str(e)}), 303)
    return RedirectResponse("/profile?message=Saved", 303)


@router.post("/profile/users")
def add_profile(request: Request, name: str = Form(...), email: str = Form(""), is_admin: bool = Form(False)):
    """Admins add profiles for friends (they then pick it at /login, or with
    Cloudflare Access, sign in with that email)."""
    if not current_user(request).is_admin:
        raise HTTPException(403, "Only admins can add profiles")
    try:
        new = users.create(name, email=email.strip() or None, is_admin=is_admin)
    except users.UserError as e:
        return RedirectResponse("/profile?" + urlencode({"error": str(e)}), 303)
    return RedirectResponse("/profile?" + urlencode({"message": f"Added {new.name}"}), 303)


@router.post("/profile/users/{user_id}/email")
def set_profile_email(request: Request, user_id: str, email: str = Form("")):
    """Admins set the email a profile signs in with through Cloudflare Access."""
    me = current_user(request)
    if not me.is_admin:
        raise HTTPException(403, "Only admins can change other profiles' emails")
    if _cloudflare_mode(request) and user_id == me.id and users.normalize_email(email) != users.normalize_email(me.email):
        return RedirectResponse("/profile?" + urlencode(
            {"error": "Changing your own email here would sign you out for good; use `tcgnecro user email`"}), 303)
    try:
        user = users.update(user_id, email=email.strip() or None)
    except users.UserError as e:
        return RedirectResponse("/profile?" + urlencode({"error": str(e)}), 303)
    return RedirectResponse("/profile?" + urlencode({"message": f"Saved {user.name}'s email"}), 303)
