"""The one Jinja environment every page renders through.

Templates are looked up in tcgnecro/templates/ (site-wide pages: base layout,
login, profile, Proxies) and then tcgnecro/deckbuilder/templates/.
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from markupsafe import Markup, escape

from tcgnecro import db

HERE = Path(__file__).parent

# Where the AGPL "Source" link in the page footer points. Must lead to the
# code you are actually running if others use your server.
SOURCE_URL = os.environ.get("TCGNECRO_SOURCE_URL", "https://github.com/mcwookie/tcg-necropolis")

templates = Jinja2Templates(directory=[HERE / "templates", HERE / "deckbuilder" / "templates"])


def ago(stamp: str | None) -> str:
    """'just now', '5 min ago', '3 h ago', '2 days ago' for an ISO timestamp."""
    if not stamp:
        return "never"
    seconds = (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds()
    if seconds < 60:
        return "just now"
    for size, unit in ((86400, "day"), (3600, "h"), (60, "min")):
        if seconds >= size:
            n = int(seconds // size)
            return f"{n} {unit}{'s' if unit == 'day' and n != 1 else ''} ago"
    return "just now"


def user_timezone(request) -> str | None:
    """The signed-in profile's chosen time zone, or None for "this device's"."""
    user = getattr(getattr(request, "state", None), "user", None)
    return user.prefs.get("timezone") if user else None


@pass_context
def when(context, stamp: str | None, fmt: str = "datetime") -> Markup:
    """A timestamp as <time>. The browser rewrites it in the viewer's time zone
    (their profile's choice, else their device's; see base.html). The text
    rendered here is the fallback: the profile's zone if set, else UTC.
    fmt: "datetime" or "date"."""
    if not stamp:
        return Markup("—")
    moment = datetime.fromisoformat(stamp)
    tz_name = user_timezone(context.get("request"))
    try:
        zone = ZoneInfo(tz_name) if tz_name else None
    except (ZoneInfoNotFoundError, ValueError):
        zone = None
    if fmt == "date":
        text = moment.astimezone(zone or timezone.utc).strftime("%Y-%m-%d")
    elif zone:
        text = moment.astimezone(zone).strftime("%Y-%m-%d %H:%M %Z")
    else:
        text = moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return Markup(f'<time datetime="{escape(stamp)}" data-fmt="{escape(fmt)}">{escape(text)}</time>')


def thumb_list(cards, pool) -> list[list]:
    """[[thumbnail url, landscape?], ...] for the cards with an image: the
    hover preview of a card shown with the cards that come with it
    (plugin.related_cards; data-thumbs in the builder)."""
    return [[db.thumb_url(pool.images[c.id], c.rotate), c.landscape] for c in cards if pool.images.get(c.id)]


templates.env.globals.update(thumb_list=thumb_list, thumb_url=db.thumb_url, image_url=db.image_url, source_url=SOURCE_URL,
                             ago=ago, when=when, user_timezone=user_timezone)
