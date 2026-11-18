"""No route takes an optional number straight from a form: browsers send an empty field as "", which a
`int | None = Form(None)` parameter rejects with a 422 (use `str = Form("")` + `form_num` / `form_int`)."""

from __future__ import annotations

import inspect
import typing

from fastapi.params import Form
from fastapi.routing import APIRoute

from fitos.main import app


def test_no_optional_numeric_form_fields():
    bad = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for name, p in inspect.signature(route.endpoint).parameters.items():
            if not isinstance(p.default, Form):
                continue
            args = set(typing.get_args(p.annotation)) if typing.get_args(p.annotation) else {p.annotation}
            if type(None) in args and args & {int, float}:
                bad.append(f"{route.path} {name}: {p.annotation}")
    assert not bad, "optional numbers from forms (use str + form_num/form_int):\n" + "\n".join(bad)


def test_finishing_with_an_empty_rpe_works(db):
    from datetime import date

    from factories import make_exercise
    from fastapi.testclient import TestClient

    from fitos.training import service as tr

    make_exercise(db, "ex1")
    plan = tr.put_custom_plan(db, date(2026, 11, 18), "Test", [{"exercise": "ex1", "sets": 1}])
    tr.start_session(db, plan, source="web")
    db.commit()
    with TestClient(app, follow_redirects=False) as c:
        r = c.post("/entreno/terminar", data={"rpe": ""})
        assert r.status_code == 303 and r.headers["location"].startswith("/entrenos/")
    assert tr.active_session(db) is None
