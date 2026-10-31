"""Fix the names of two-sided Marvel Champions card images in a collection.

The catalog follows MarvelCDB (see this package's __init__): a two-sided card
is its *front* code, and the other face is that card's `~back` image. Some
collections name faces differently, and those images then match no card:

- the front filed under the plain code:   27088@sm.jpg   ->  27088a@sm.jpg
- the back filed under the hidden code:   27088b@sm.jpg  ->  27088a@sm~back.jpg

Only a "b" file whose card's front image exists can be renamed with
certainty. Anything else needs a look: plain-code files have turned out to be
fronts in some cards and backs in others, and some "b" files hold the front
(04159b@trors.jpg shows the "a" card). So this works in two steps:

    python -m tcgnecro.games.marvel_champions.faces plan [FOLDER]
        Writes faces-plan.tsv (one rename per line; review lines are marked)
        and faces-review.html (each uncertain image beside its "a" and "b"
        card names). Changes nothing.

    python -m tcgnecro.games.marvel_champions.faces apply faces-plan.tsv
        Performs the renames in the plan, never overwriting a file.
        Then reindex wherever TCG Necropolis runs: `tcgnecro reindex`, or
        `docker compose restart app` for the Docker container.

Edit the plan's ACTION column before applying: `back`, `front` or `skip`.
FOLDER defaults to every Marvel Champions collection under the data folder
(`data/` here, or TCGNECRO_DATA). The plan and review page are written to the
current directory.
"""

import base64
import csv
import html
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from tcgnecro import db
from tcgnecro.games.marvel_champions import cache_dir, printed_cards
from tcgnecro.naming import ImageName, parse_filename

GAME_ID = "marvel_champions"
PLAN_FIELDS = ["ACTION", "FOLDER", "FILE", "AS_FRONT", "AS_BACK", "CERTAIN", "WHY"]


@dataclass
class Rename:
    folder: Path
    file: str
    as_front: str       # the new name if this image is the card's front
    as_back: str        # the new name if it's the back ("" when not applicable)
    action: str         # "front", "back" or "skip"
    certain: bool
    why: str


def image_name(card_id: str, parsed: ImageName, side: str, ext: str) -> str:
    """{card}@{printing}[~side][.bleed].ext"""
    return (f"{card_id}@{parsed.printing}" + ("" if side == "front" else f"~{side}")
            + (".bleed" if parsed.has_bleed else "") + ext)


def plan(folder: Path, cards: list[dict]) -> list[Rename]:
    """Renames for the images in one collection folder whose code matches no
    printed card. Images that already match are left alone, and no rename is
    ever planned onto a name that's taken.

    Only one case is certain: a second-face ("b") file whose card's front
    image exists is that card's back. Everything else gets a default and is
    marked for review, because collections have proved both ways wrong: a
    plain-code file was the front in one card and the 1B back in another,
    and some "b" files hold the front.
    """
    printed = {c["code"] for c in printed_cards(cards)}
    front_of = {c["linked_to_code"]: c["code"] for c in cards
                if c.get("linked_to_code") and c["code"] in printed}
    files = {f.name: parse_filename(f.name) for f in sorted(folder.iterdir()) if f.is_file()}
    files = {name: p for name, p in files.items() if p}
    taken = set(files)
    fronts = {(p.card_id, p.printing) for p in files.values() if p.side == "front"}

    renames = []
    for name, p in files.items():
        if p.card_id in printed or p.side != "front":
            continue
        if (p.card_id + "a") in printed:          # plain code of a two-sided card
            card, kind = p.card_id + "a", "plain"
        elif p.card_id in front_of:               # the hidden second face's code
            card, kind = front_of[p.card_id], "hidden"
        else:
            continue
        ext = Path(name).suffix
        as_front, as_back = image_name(card, p, "front", ext), image_name(card, p, "back", ext)
        has_front = (card, p.printing) in fronts
        if kind == "hidden" and has_front:
            action, certain, why = "back", True, f"back face of {card}"
        elif kind == "hidden":
            action, certain, why = "back", False, f"back face of {card}, whose front has no image: check it isn't the front"
        elif has_front:
            action, certain, why = "back", False, f"plain code {p.card_id}; {card}'s front exists, so probably the back (check)"
        else:
            action, certain, why = "front", False, f"plain code {p.card_id}: probably {card}'s front (check)"
        target = as_front if action == "front" else as_back
        if target in taken:                       # never onto a taken name
            other_action = "back" if action == "front" else "front"
            other = as_back if action == "front" else as_front
            if other in taken:
                action, certain, why = "skip", False, f"both {as_front} and {as_back} exist: duplicate?"
            else:
                action, certain, why = other_action, False, f"{target} exists, so planned as the {other_action} (check)"
        if action != "skip":
            taken.add(as_front if action == "front" else as_back)
            if action == "front":
                fronts.add((card, p.printing))
        renames.append(Rename(folder, name, as_front, as_back, action, certain, why))
    return renames


def write_plan(renames: list[Rename], path: Path) -> None:
    with open(path, "w", newline="") as f:
        out = csv.writer(f, delimiter="\t")
        out.writerow(PLAN_FIELDS)
        for r in renames:
            out.writerow([r.action, r.folder, r.file, r.as_front, r.as_back,
                          "yes" if r.certain else "REVIEW", r.why])


def write_review(renames: list[Rename], cards: list[dict], path: Path) -> int:
    """A self-contained page showing each uncertain image beside both faces'
    names (thumbnails embedded, so it opens anywhere). Returns how many."""
    from PIL import Image

    by_code = {c["code"]: c for c in cards}
    rows = []
    for n, r in enumerate((r for r in renames if not r.certain), 1):
        code = parse_filename(r.file).card_id
        front_code = parse_filename(r.as_front).card_id
        im = Image.open(r.folder / r.file).convert("RGB")
        im.thumbnail((300, 420))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        rows.append(f"""<tr><td>{n}</td>
  <td><img src="data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}"></td>
  <td><code>{html.escape(r.file)}</code><br><br>
      <b>back</b> (default) &rarr; <code>{html.escape(r.as_back)}</code>, if it shows
      &ldquo;{html.escape(by_code.get(code, {}).get("name", "?"))}&rdquo;<br><br>
      <b>front</b> &rarr; <code>{html.escape(r.as_front)}</code>, if it shows
      &ldquo;{html.escape(by_code.get(front_code, {}).get("name", "?"))}&rdquo;</td></tr>""")
    path.write_text(f"""<!doctype html><meta charset="utf-8"><title>Marvel faces to review</title>
<style>body{{font:15px system-ui;margin:20px}} td{{vertical-align:top;padding:8px;border-bottom:1px solid #ccc}}
img{{max-width:300px;max-height:420px}}</style>
<h1>{len(rows)} card faces to review</h1>
<p>Each image is named as a card's second face, but that card's front has no image. Most are the
back face. Where an image shows the <b>front</b> card instead, change its line in the plan's ACTION
column from <code>back</code> to <code>front</code> (or <code>skip</code> to leave it), then apply the plan.
Where both faces share a name (many main schemes), look at the stage marker instead:
<b>A</b> (e.g. 2A) is the front, <b>B</b> (e.g. 2B) the back.</p>
<table>{''.join(rows)}</table>""")
    return len(rows)


def apply(plan_path: Path) -> tuple[int, list[str]]:
    """Perform a (reviewed) plan. Returns (renamed, problems)."""
    renamed, problems = 0, []
    with open(plan_path, newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            action = row["ACTION"].strip().lower()
            if action == "skip":
                continue
            if action not in ("front", "back") or (action == "back" and not row["AS_BACK"]):
                problems.append(f"{row['FILE']}: unknown action '{row['ACTION']}'")
                continue
            folder = Path(row["FOLDER"])
            source = folder / row["FILE"]
            target = folder / (row["AS_FRONT"] if action == "front" else row["AS_BACK"])
            if not source.exists():
                problems.append(f"{row['FILE']}: not found (already renamed?)")
            elif target.exists():
                problems.append(f"{row['FILE']}: {target.name} already exists; left alone")
            else:
                source.rename(target)
                renamed += 1
    return renamed, problems


def main(argv: list[str]) -> None:
    if not argv or argv[0] not in ("plan", "apply"):
        sys.exit(__doc__)
    if argv[0] == "apply":
        renamed, problems = apply(Path(argv[1] if len(argv) > 1 else "faces-plan.tsv"))
        for p in problems:
            print("  " + p)
        print(f"Renamed {renamed} file(s). Now reindex wherever TCG Necropolis runs: "
              "`tcgnecro reindex`, or `docker compose restart app` for the Docker container.")
        return
    cards_file = cache_dir() / "cards.json"
    if not cards_file.exists():
        sys.exit(f"No MarvelCDB data at {cards_file}. Run this from the TCG Necropolis folder, or set "
                 "TCGNECRO_DATA to your data folder (and run `tcgnecro catalog marvel_champions` once).")
    cards = json.loads(cards_file.read_text())
    folders = ([Path(argv[1])] if len(argv) > 1
               else sorted(p for p in (db.collections_dir() / GAME_ID).iterdir() if p.is_dir()))
    renames = [r for folder in folders for r in plan(folder, cards)]
    write_plan(renames, Path("faces-plan.tsv"))
    reviews = write_review(renames, cards, Path("faces-review.html"))
    print(f"{len(renames)} rename(s) planned: {len(renames) - reviews} certain, {reviews} to review.")
    print("Wrote faces-plan.tsv" + (" and faces-review.html (open it in a browser)" if reviews else "") + ".")
    print("Check the plan, then: python -m tcgnecro.games.marvel_champions.faces apply faces-plan.tsv")


if __name__ == "__main__":
    main(sys.argv[1:])
