"""The collection image naming convention, shared with upstream:

    {card_id}@{printing}[~{side}][.bleed].{ext}

`side` is omitted for the front, otherwise `back`, `back2`, `back3`, ...
"""

from dataclasses import dataclass
from pathlib import PurePath

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


@dataclass(frozen=True)
class ImageName:
    card_id: str
    printing: str
    side: str
    has_bleed: bool


def parse_filename(name: str) -> ImageName | None:
    path = PurePath(name)
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        return None

    stem = path.stem
    has_bleed = stem.endswith(".bleed")
    if has_bleed:
        stem = stem[: -len(".bleed")]

    card_id, sep, rest = stem.partition("@")
    if not sep or "@" in rest:
        return None

    printing, sep, side = rest.partition("~")
    if not sep:
        side = "front"
    elif "~" in side:
        return None

    return ImageName(card_id, printing, side, has_bleed)


def back_index(side: str) -> int | None:
    """`back` -> 1, `back2` -> 2, anything else -> None."""
    if not side.startswith("back"):
        return None
    rest = side[len("back"):]
    if rest == "":
        return 1
    if rest.isdigit() and int(rest) >= 1:
        return int(rest)
    return None


def side_sort_key(side: str) -> int:
    return 0 if side == "front" else back_index(side) or 0


def validate(names: list[ImageName]) -> list[str]:
    """The same checks upstream runs on import: every printing has a front,
    and backs are numbered from one with no gaps. Returns error messages."""
    errors = []
    sides: dict[tuple[str, str], set[str]] = {}
    for n in names:
        if n.side != "front" and back_index(n.side) is None:
            errors.append(f"{n.card_id}@{n.printing}: unknown side '{n.side}'")
            continue
        sides.setdefault((n.card_id, n.printing), set()).add(n.side)

    for (card_id, printing), found in sorted(sides.items()):
        if "front" not in found:
            errors.append(f"{card_id}@{printing}: has other sides but no front image")
        indices = {back_index(s) for s in found if s != "front"}
        if indices:
            missing = [i for i in range(1, max(indices) + 1) if i not in indices]
            if missing:
                labels = ", ".join("back" if i == 1 else f"back{i}" for i in missing)
                errors.append(f"{card_id}@{printing}: missing {labels}")
    return errors
