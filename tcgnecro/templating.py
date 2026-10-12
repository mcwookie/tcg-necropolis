"""The one Jinja environment every page renders through.

Templates are looked up in tcgnecro/templates/ (site-wide pages: base layout,
login, profile, Proxies) and then tcgnecro/deckbuilder/templates/.
"""

import os
from pathlib import Path

from fastapi.templating import Jinja2Templates

from tcgnecro import db

HERE = Path(__file__).parent

# Where the AGPL "Source" link in the page footer points. Must lead to the
# code you are actually running if others use your server.
SOURCE_URL = os.environ.get("TCGNECRO_SOURCE_URL", "https://github.com/mcwookie/tcg-necropolis")

templates = Jinja2Templates(directory=[HERE / "templates", HERE / "deckbuilder" / "templates"])
templates.env.globals.update(thumb_url=db.thumb_url, image_url=db.image_url, source_url=SOURCE_URL)
