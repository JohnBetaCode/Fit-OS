"""`fitos labs …`: diccionario de biomarcadores e importación de la semilla."""

from __future__ import annotations

from pathlib import Path

from ..i18n import _


def cmd_ensure_biomarkers(args) -> int:
    from ..core.db import session_scope
    from .service import ensure_biomarkers

    with session_scope() as db:
        n = ensure_biomarkers(db, args.yaml)
    print("labs ensure-biomarkers: " + _("{n} biomarcadores", n=n))
    return 0 if n else 1


def cmd_import_seed(args) -> int:
    from ..core.db import session_scope
    from .seed_import import import_seed_csv

    path = Path(args.csv)
    if not path.is_file():
        print("labs import-seed: " + _("no existe {path}", path=path))
        return 1
    with session_scope() as db:
        r = import_seed_csv(db, path)
    print("labs import-seed: " + _("{reports} informes, {measured} medidos, {derived} derivados, {review} por "
                                   "confirmar", reports=r.reports, measured=r.measured, derived=r.derived,
                                   review=r.needs_review))
    for label, items in ((_("sin mapear"), r.unmapped), (_("celdas omitidas"), r.skipped_cells),
                         (_("TG/HDL distinto del CSV"), r.ratio_mismatches)):
        if items:
            print(f"  {label}: {', '.join(items)}")
    return 0


def register(sub) -> None:
    labs = sub.add_parser("labs", help="Exámenes de laboratorio").add_subparsers(dest="labs_cmd", required=True)
    s = labs.add_parser("import-seed", help="Importa la semilla CSV exportada de Notion")
    s.add_argument("csv")
    s.set_defaults(fn=cmd_import_seed)
    e = labs.add_parser("ensure-biomarkers", help="Sincroniza catalog/biomarkers.yaml con la base")
    e.add_argument("--yaml", default=None, help="Ruta alternativa al YAML")
    e.set_defaults(fn=cmd_ensure_biomarkers, scope="each")
