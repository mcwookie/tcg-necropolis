"""Command line: `tcgnecro --help`."""

import argparse
import sys
import urllib.error
from pathlib import Path

from tcgnecro import collections, db
from tcgnecro.decklist import pack_requests, parse_decklist, resolve
from tcgnecro.games import GAME_MODULES, get_game
from tcgnecro.pdf import CUT_LINES, LAYOUTS, PAGE_SIZES, PdfOptions, generate_pdf


def cmd_catalog(args):
    conn = db.connect()
    for game_id in args.games or GAME_MODULES:
        try:
            catalog = get_game(game_id).load_catalog(refresh=args.refresh)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"{game_id}: couldn't download card data ({e}); keeping what was loaded before",
                  file=sys.stderr)
            continue
        db.store_catalog(conn, catalog)
        print(f"{catalog.name}: {len(catalog.packs)} packs, {len(catalog.cards)} cards")


def cmd_import(args):
    conn = db.connect()
    target = collections.import_archive(Path(args.archive), args.name)
    print(f"Extracted to {target}")
    _reindex(conn)


def cmd_reindex(args):
    _reindex(db.connect())


def _reindex(conn):
    for name, count in collections.reindex(conn).items():
        print(f"{name}: {count} images")


def cmd_list(args):
    conn = db.connect()
    rows = conn.execute(
        """SELECT c.name, c.game_id, c.version, COUNT(i.file_path) AS images
           FROM collections c LEFT JOIN images i ON i.collection = c.name GROUP BY c.name"""
    ).fetchall()
    if not rows:
        print("No collections. Use `tcgnecro import ARCHIVE`.")
    for r in rows:
        print(f"{r['name']:30} {r['game_id']:12} v{r['version'] or '?':8} {r['images']} images")


def cmd_remove(args):
    collections.remove(args.name)
    _reindex(db.connect())


def cmd_pdf(args):
    conn = db.connect()
    if args.pack:
        res = resolve(conn, args.game, pack_requests(conn, args.game, args.pack))
    else:
        text = sys.stdin.read() if args.deck == "-" else Path(args.deck).read_text()
        requests, not_found = parse_decklist(conn, args.game, text)
        res = resolve(conn, args.game, requests, not_found)
    for name in res.not_found:
        print(f"warning: not in catalog: {name}", file=sys.stderr)
    for name in res.no_image:
        print(f"warning: no image: {name}", file=sys.stderr)

    options = PdfOptions(args.page, args.layout, args.cut_lines, args.cut_line_thickness,
                         args.double_sided, args.back_label, args.compact)

    def progress(done, total):
        print(f"\r  {done}/{total} images", end="", file=sys.stderr, flush=True)

    Path(args.output).write_bytes(generate_pdf(res.printings, options, args.game, progress))
    print(f"\nWrote {args.output} ({len(res.printings)} cards)", file=sys.stderr)


def cmd_serve(args):
    import uvicorn
    uvicorn.run("tcgnecro.web:app", host=args.host, port=args.port, reload=args.reload)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tcgnecro", description="TCG Necropolis")
    sub = parser.add_subparsers(required=True)

    p = sub.add_parser("catalog", help="load card catalogs for supported games")
    p.add_argument("games", nargs="*", choices=GAME_MODULES + [[]], metavar="GAME")
    p.add_argument("--refresh", action="store_true",
                   help="re-download data for games that fetch it (e.g. ahlcg from ArkhamDB)")
    p.set_defaults(func=cmd_catalog)

    p = sub.add_parser("import", help="unzip a collection archive into the collections folder")
    p.add_argument("archive")
    p.add_argument("--name", help="collection name (default: file name)")
    p.set_defaults(func=cmd_import)

    sub.add_parser("reindex", help="rescan collection folders").set_defaults(func=cmd_reindex)
    sub.add_parser("list", help="list collections").set_defaults(func=cmd_list)

    p = sub.add_parser("remove", help="delete a collection folder")
    p.add_argument("name")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("pdf", help="build a print-and-play PDF")
    p.add_argument("--game", required=True, choices=GAME_MODULES)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--deck", help="decklist file, or - for stdin")
    src.add_argument("--pack", help="print a whole pack by id")
    p.add_argument("-o", "--output", default="proxies.pdf")
    p.add_argument("--page", choices=list(PAGE_SIZES), default="letter")
    p.add_argument("--layout", choices=LAYOUTS, default="edge")
    p.add_argument("--cut-lines", choices=CUT_LINES, default="margins")
    p.add_argument("--cut-line-thickness", type=float, default=0.5)
    p.add_argument("--double-sided", action="store_true")
    p.add_argument("--back-label", help="generic card back label, e.g. proxy")
    p.add_argument("--compact", action="store_true", help="300 DPI JPEG images, much smaller PDF")
    p.set_defaults(func=cmd_pdf)

    p = sub.add_parser("serve", help="run the web UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (collections.CollectionError, KeyError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
