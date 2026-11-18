"""Subcomandos `fitos catalog fetch|build-media|import`."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..core.settings import get_settings
from ..i18n import _


def catalog_dir() -> Path:
    """Directorio versionado `catalog/`: `FITOS_CATALOG_DIR`, o `/catalog` (contenedor), o el del repo."""
    if env := os.environ.get("FITOS_CATALOG_DIR"):
        return Path(env)
    if Path("/catalog").is_dir():
        return Path("/catalog")
    return Path(__file__).resolve().parents[3] / "catalog"


def _src_dir() -> Path:
    return get_settings().data_dir / "catalog" / "src"


def cmd_fetch(_args) -> int:
    from .importer import fetch

    res = fetch(get_settings().data_dir, catalog_dir() / "source.lock")
    print(f"catalog fetch: {res['action']} {res['path']} @ {res['commit']}")
    return 0


def cmd_build_media(args) -> int:
    from .media import build_media

    res = build_media(
        _src_dir(), get_settings().data_dir / "catalog", limit=args.limit, with_audio=not args.no_audio
    )
    for ex_id, err in res["failed"]:
        print("  " + _("falló {ex_id}: {err}", ex_id=ex_id, err=err))
    print("catalog build-media: " + json.dumps({**res, "failed": len(res["failed"])}))
    return 1 if res["failed"] else 0


def cmd_import(_args) -> int:
    from ..core.db import session_scope
    from .importer import import_catalog

    cat = catalog_dir()
    with session_scope() as db:
        res = import_catalog(db, _src_dir(), cat / "enrichment.json", cat / "equipment_map.yaml")
    print("catalog import: " + json.dumps(res, ensure_ascii=False))
    return 0


def cmd_search(args) -> int:
    """`fitos catalog search "remo"`: exercise ids for plans, filtered by muscle, equipment or a place's equipment."""
    from ..body.service import equipment_profiles
    from ..core.db import session_scope
    from .service import search

    with session_scope() as db:
        tags = None
        if args.place:
            ep = next((e for e in equipment_profiles(db) if args.place in (str(e.id), e.name.lower(), e.kind)), None)
            if ep is None:
                raise SystemExit(f"catalog search: no place {args.place!r}")
            tags = ep.tags
        rows = search(db, args.query, muscle=args.muscle or None, equipment_tag=args.equipment or None,
                      profile_tags=tags, limit=args.limit)
        for e in rows:
            print(f"{e.id}\t{e.name_es}\t{e.primary_muscle}\t{e.equipment_tag}\t{e.mechanic or ''}"
                  f"{chr(9) + 'staple' if e.is_staple else ''}")
    return 0


def register(sub) -> None:
    cat = sub.add_parser("catalog", help="catálogo de ejercicios").add_subparsers(
        dest="catalog_cmd", required=True
    )
    cat.add_parser("fetch", help="clona o actualiza el dataset en $DATA_DIR/catalog/src").set_defaults(
        fn=cmd_fetch, scope="none"
    )
    bm = cat.add_parser("build-media", help="GIF → MP4 y miniaturas")
    bm.add_argument("--limit", type=int, default=None)
    bm.add_argument("--no-audio", action="store_true", help="sin pista AAC muda")
    bm.set_defaults(fn=cmd_build_media, scope="none")
    se = cat.add_parser("search", help="find exercise ids: name words, --muscle, --equipment, --place")
    se.add_argument("query", nargs="?", default="")
    se.add_argument("--muscle", default="")
    se.add_argument("--equipment", default="")
    se.add_argument("--place", default="", help="an equipment profile: id, name or gym/home")
    se.add_argument("--limit", type=int, default=15)
    se.set_defaults(fn=cmd_search)
    cat.add_parser("import", help="upsert de exercises (every account unless --user)").set_defaults(
        fn=cmd_import, scope="each"
    )
