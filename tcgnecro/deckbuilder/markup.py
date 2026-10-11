"""Card text -> safe HTML.

Card data comes from outside sources, so it is never trusted as HTML. The
text is escaped first, and then only a short list of harmless tags is turned
back into markup. Game-specific notation is handled after escaping:

- [[Trait]]  -> bold italic trait name (ArkhamDB style)
- [token]    -> an icon badge, as configured in deckbuilder.toml [icons]
- newlines   -> <br>
"""

import html
import re

from markupsafe import Markup

ALLOWED_TAGS = ("b", "i", "em", "strong", "u", "cite", "blockquote")
VOID_TAGS = ("br", "hr")

_TAG = re.compile(
    r"&lt;(/?)(" + "|".join(ALLOWED_TAGS + VOID_TAGS) + r")\s*/?&gt;", re.IGNORECASE
)
_TRAIT = re.compile(r"\[\[([^\[\]]+)\]\]")
_TOKEN = re.compile(r"\[([a-z0-9_]+)\]")


def render(text: str, icons: dict) -> Markup:
    out = html.escape(text, quote=True)
    out = _TAG.sub(lambda m: f"<{m.group(1)}{m.group(2).lower()}>", out)
    out = _TRAIT.sub(r'<b class="trait"><i>\1</i></b>', out)

    def icon(m: re.Match) -> str:
        token = m.group(1)
        conf = icons.get(token)
        if conf is None:
            return m.group(0)
        css = f"icon icon-{token} {conf.css or ''}".strip()
        title = html.escape(conf.title or token.replace("_", " "))
        return f'<span class="{css}" title="{title}">{html.escape(conf.label)}</span>'

    out = _TOKEN.sub(icon, out)
    out = out.replace("\n", "<br>")
    return Markup(out)
