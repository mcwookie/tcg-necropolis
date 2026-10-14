"""The one Jinja environment every page renders through.

Templates are looked up in tcgnecro/templates/ (site-wide pages: base layout,
login, profile, Proxies) and then tcgnecro/deckbuilder/templates/.
"""

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi.templating import Jinja2Templates

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


templates.env.globals.update(thumb_url=db.thumb_url, image_url=db.image_url, source_url=SOURCE_URL, ago=ago)
