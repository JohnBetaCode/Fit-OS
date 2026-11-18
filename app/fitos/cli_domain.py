"""Subcomandos de dominio de `fitos`: catálogo, exámenes, perfil y planes."""

from __future__ import annotations

from pathlib import Path


def _profile_import(args) -> int:
    from .body.service import import_profile_yaml
    from .core.db import session_scope

    with session_scope() as db:
        p = import_profile_yaml(db, Path(args.path))
        print(f"profile import: sexo={p.sex} nacimiento={p.birth_date} estatura={p.height_cm}")
    return 0


def _plan_put(args) -> int:
    """`fitos plan put plan.yaml`: a hand-made plan for one day (see `training.service.put_custom_plan`)."""
    from datetime import date

    import yaml

    from .core.db import session_scope
    from .training.service import active_program, put_custom_plan

    spec = yaml.safe_load(Path(args.path).read_text(encoding="utf-8"))
    on = spec["date"] if isinstance(spec["date"], date) else date.fromisoformat(str(spec["date"]))
    with session_scope() as db:
        plan = put_custom_plan(db, on, spec.get("focus") or "Custom", spec["items"],
                               equipment_profile_id=spec.get("equipment_profile_id"), rationale=spec.get("rationale"))
        # `program_day: next` (or a day's name): finishing the plan moves the program's rotation like a planned day
        want = spec.get("program_day")
        prog = active_program(db)
        if want and prog and prog.days:
            day = (prog.days[prog.rotation_idx % len(prog.days)] if str(want) == "next"
                   else next((d for d in prog.days if d.name.lower() == str(want).lower()), None))
            if day is None:
                raise SystemExit(f"plan put: no program day {want!r} (days: {', '.join(d.name for d in prog.days)})")
            plan.program_day_id = day.id
        _print_plan(plan)
        if args.dry_run:
            db.rollback()
            print("dry run: nothing saved")
    return 0


def _context(args) -> int:
    """`fitos context`: what a coach needs to plan a day, as JSON (see coach/context.py)."""
    import json
    from datetime import date

    from .coach.context import build
    from .core.db import session_scope

    with session_scope() as db:
        data = build(db, days=args.days, on=date.fromisoformat(args.date) if args.date else None)
        db.rollback()  # read-only, even for defaults it may create
    print(json.dumps(data, ensure_ascii=False, indent=1, default=str))
    return 0


def _plan_show(args) -> int:
    from datetime import date

    from .core.db import session_scope
    from .core.timeutil import local_today
    from .training.service import plan_for

    on = date.fromisoformat(args.date) if args.date else local_today()
    with session_scope() as db:
        plan = plan_for(db, on)
        if plan is None:
            print(f"no plan for {on}")
            return 1
        _print_plan(plan)
    return 0


def _print_plan(plan) -> None:
    from .wa.cards import rep_range, weight_text

    print(f"{plan.date} · {plan.focus_label} · {plan.status} · source={plan.source} · id={plan.id}")
    for n, it in enumerate(plan.items, 1):
        w = weight_text(it.target_weight_kg, it.exercise)
        print(f"{n}. [{it.exercise_id}] {it.exercise.display_name} — {it.sets} × {rep_range(it)}"
              f"{' · ' + w if w else ''} · RIR {it.target_rir}{' · ' + it.target_note if it.target_note else ''}")


def _export(args) -> int:
    from .core.db import session_scope
    from .export import build_zip

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with session_scope() as db:
        out.write_bytes(build_zip(db))
    out.chmod(0o600)
    print(f"export: {out}")
    return 0


def register(sub) -> None:
    from .catalog.cli import register as catalog_register
    from .demo import register as demo_register
    from .labs.cli import register as labs_register

    catalog_register(sub)
    labs_register(sub)
    demo_register(sub)
    prof = sub.add_parser("profile").add_subparsers(dest="profile_cmd", required=True)
    imp = prof.add_parser("import")
    imp.add_argument("path")
    imp.set_defaults(fn=_profile_import)
    plan = sub.add_parser("plan").add_subparsers(dest="plan_cmd", required=True)
    put = plan.add_parser("put", help="hand-made plan for one day from a YAML file")
    put.add_argument("path")
    put.add_argument("--dry-run", action="store_true", help="validate and print, save nothing")
    put.set_defaults(fn=_plan_put)
    ctx = sub.add_parser("context", help="what a coach needs to plan a day (JSON)")
    ctx.add_argument("--days", type=int, default=42, help="how many days of sessions (default 42)")
    ctx.add_argument("--date", default="", help="plan for this date (default today)")
    ctx.set_defaults(fn=_context)
    show = plan.add_parser("show", help="the plan of a day (default today)")
    show.add_argument("date", nargs="?")
    show.set_defaults(fn=_plan_show)
    exp = sub.add_parser("export", help="everything you logged, in one ZIP (contains health data)")
    exp.add_argument("out")
    exp.set_defaults(fn=_export)
