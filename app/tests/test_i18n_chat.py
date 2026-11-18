"""WhatsApp chat, training notes and coach in English, and commands in both languages whatever the preference."""

from __future__ import annotations

import pytest
from factories import make_catalog, make_equipment_profiles, make_profile
from sqlalchemy import select

from fitos.coach import agent
from fitos.coach import llm as llm_mod
from fitos.coach.prompts import SYSTEM_PROMPTS
from fitos.core.db import session_scope
from fitos.core.models import BodyMetric, WorkoutSession
from fitos.i18n import _, en_catalog, set_lang
from fitos.training import progression
from fitos.training import service as tr
from fitos.training.planner import create_program_from_template
from fitos.training.substitution import word_to_tag
from fitos.wa import cards, outbox, router, session_flow
from fitos.wa.parser import SetEntry, Target, describe, parse_set_log

DAY = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
SPANISH = ["Descanso", "descanso", "Objetivo", "Siguiente", "Saltado", "Sesión", "ocupada", "Responde", "Última",
           "Empezar", "ejercicios", "Anotado", "¿Qué", "dura"]


@pytest.fixture
def world():
    with session_scope() as db:
        make_catalog(db)
        _casa, gym = make_equipment_profiles(db)
        make_profile(db, sex="male", days_per_week=4, onboarded=True,
                     schedule={d: {"equipment_profile_id": gym.id, "time": "06:30"} for d in DAY},
                     injuries=[], disliked_exercise_ids=[])
        create_program_from_template(db, None, 4)


async def say(text: str, fake) -> list[str]:
    n0 = len(fake.sent)
    await session_flow.handle_text(0, text)
    await outbox.drain_once()
    return [s.text for s in fake.sent[n0:]]


def no_spanish(text: str) -> None:
    found = [w for w in SPANISH if w in text]
    assert not found, (found, text)


# ── Session in English ──────────────────────────────────────────────────────


async def test_english_session(world, fake_wa):
    set_lang("en")
    out = await say("today", fake_wa)
    assert "*Today:" in out[0] and "*1* Start" in out[0] and "exercises" in out[0]
    assert "Home" in out[0] and "Casa" not in out[0]  # default profile names are translated
    no_spanish(out[0])

    out = await say("start", fake_wa)
    assert "Let's go" in out[0] and "Target:" in out[0] and "Reply:" in out[0]
    no_spanish(out[0])

    out = await say("8x40 r2", fake_wa)
    assert "Set 1: 8 reps" in out[0] and "Next: set 2" in out[0] and "Rest" in out[0]
    no_spanish(out[0])

    out = await say("busy", fake_wa)
    assert "station busy" in out[0] and "Switching to" in out[0]
    no_spanish(out[0])

    out = await say("skip", fake_wa)
    assert "Skipped" in out[0]
    no_spanish(out[0])

    out = await say("finish", fake_wa)
    assert "How hard was the session" in out[0]
    out = await say("7", fake_wa)
    assert "Session done" in out[0] and "kg lifted" in out[0] and "Session RPE: 7/10" in out[0]
    no_spanish(out[0])
    with session_scope() as db:
        ws = db.scalar(select(WorkoutSession).order_by(WorkoutSession.id.desc()))
        assert ws.state == "completed" and ws.session_rpe == 7


async def test_english_decimals_and_more_commands(world, fake_wa):
    set_lang("en")
    out = await say("weight 80.5", fake_wa)
    assert "Logged: 80.5 kg" in out[0]
    out = await say("status", fake_wa)
    assert "No active session" in out[0]
    await say("let's go", fake_wa)
    out = await say("10 reps with 22.5", fake_wa)
    assert "22.5 kg" in out[0] and "22,5" not in out[0]
    out = await say("fix 8x40", fake_wa)
    assert "Fixed: 8 reps" in out[0]
    out = await say("one more", fake_wa)
    assert "One more set" in out[0]
    out = await say("where am i", fake_wa)
    assert "session" in out[0] and "sets total" in out[0]
    out = await say("undo", fake_wa)
    assert "Deleted the last set" in out[0]
    out = await say("no barbell", fake_wa)
    assert "no barbell" in out[0] or "No more alternatives" in out[0]
    out = await say("done", fake_wa)  # finishes the current exercise, not the session
    with session_scope() as db:
        assert tr.active_session(db).state == "active"


async def test_weight_in_pounds(world, fake_wa):
    set_lang("en")
    out = await say("bw 180 lbs", fake_wa)
    assert "Logged: 81.6 kg" in out[0]
    with session_scope() as db:
        assert db.scalar(select(BodyMetric.value)) == pytest.approx(81.6)


async def test_rest_day_in_english(world, fake_wa):
    set_lang("en")
    await say("plan", fake_wa)
    out = await say("rest day", fake_wa)
    assert "rest day" in out[0].lower() and "rotation" in out[0]


# ── Commands in both languages whatever the preference ─────────────────────


async def test_spanish_commands_with_english_preference(world, fake_wa):
    set_lang("en")
    out = await say("hoy", fake_wa)
    assert "*Today:" in out[0]
    out = await say("empezar", fake_wa)
    assert "Target:" in out[0]
    out = await say("10x40", fake_wa)
    assert "Set 1" in out[0] and "Next: set 2" in out[0]
    out = await say("ocupada", fake_wa)
    assert "station busy" in out[0]
    out = await say("salta", fake_wa)
    assert "Skipped" in out[0]
    out = await say("fin", fake_wa)
    assert "How hard" in out[0]
    out = await say("no", fake_wa)
    assert "Session done" in out[0]


async def test_english_commands_with_spanish_preference(world, fake_wa):
    set_lang("es")
    out = await say("what's today", fake_wa)
    assert "Hoy toca" in out[0] and "*1* Empezar" in out[0]
    out = await say("go", fake_wa)
    assert "Objetivo:" in out[0]
    out = await say("10 at 40", fake_wa)
    assert "Set 1: 10 reps" in out[0] and "Siguiente: set 2" in out[0]
    out = await say("taken", fake_wa)
    assert "estación ocupada" in out[0]
    out = await say("skip", fake_wa)
    assert "Saltado" in out[0]
    out = await say("done for today", fake_wa)
    assert "de *1* a *10*" in out[0]
    out = await say("skip", fake_wa)
    assert "Sesión cerrada" in out[0]


def test_help_follows_language():
    set_lang("en")
    txt = router.help_text()
    assert "*today*" in txt and "*busy*" in txt and "*weight 80.5*" in txt
    no_spanish(txt)
    set_lang("es")
    txt = router.help_text()
    assert "*hoy*" in txt and "*ocupada*" in txt and "*peso 80.5*" in txt
    assert "help" in router.HELP_WORDS and "ayuda" in router.HELP_WORDS and "commands" in router.HELP_WORDS


def test_command_sets_have_both_languages():
    sf = session_flow
    for es, en, group in [("hoy", "today", sf.PLAN_WORDS), ("empezar", "start", sf.START), ("ocupada", "busy", sf.BUSY),
                          ("otra", "another", sf.NEXT_ALT), ("salta", "skip", sf.SKIP), ("listo", "done", sf.DONE_ITEM),
                          ("como", "how", sf.HOWTO), ("deshacer", "undo", sf.UNDO), ("uno mas", "one more", sf.EXTRA),
                          ("fin", "finish", sf.FINISH), ("estado", "status", sf.STATUS),
                          ("descanso", "rest day", sf.REST_DAY), ("otro enfoque", "other focus", sf.OTHER_FOCUS),
                          ("cambiar lugar", "change place", sf.SWITCH), ("video", "clip", sf.VIDEO)]:
        assert es in group and en in group, (es, en)
    # "done" closes the exercise; finishing the session needs an explicit phrase.
    assert "done" not in sf.FINISH and "done for today" not in sf.DONE_ITEM
    for text, weight in [("peso 80,5", "80,5"), ("weight 80.5", "80.5"), ("bw 80.5", "80.5"), ("i weigh 80", "80")]:
        m = sf.RE_PESO.match(text)
        assert m and m.group(1) == weight, text
    for text in ["no tengo barra", "no barbell", "don't have a barbell", "i dont have barbell", "without barbell",
                 "sin barra"]:
        m = sf.RE_NO_TENGO.match(text)
        assert m and word_to_tag(m.group(1)) == "barbell", text
    assert sf.RE_CORRIGE.match("fix 10x40").group(1) == "10x40"


@pytest.mark.parametrize("words,tag", [
    ("dumbbells", "dumbbell"), ("a bench", "bench_flat"), ("cable", "cable"), ("machine", "machine"),
    ("squat rack", "rack"), ("bar", "barbell"), ("pull up bar", "pullup_bar"), ("kettlebell", "kettlebell"),
    ("bands", "band"), ("smith machine", "smith"), ("mancuernas", "dumbbell"), ("incline bench", "bench_incline"),
])
def test_english_equipment_words(words, tag):
    assert word_to_tag(words) == tag


# ── Parser: English forms ───────────────────────────────────────────────────


def E(reps=None, w=None, rir=None, dur=None, bw=False, added=None):
    return SetEntry(reps, w, rir, dur, bw, added)


T40 = Target(8, 12, 40)
TDUR = Target(30, 60, None, load_kind="duration")


@pytest.mark.parametrize("text,target,expected", [
    ("10 reps with 40", None, [E(10, 40)]),
    ("10 at 40", None, [E(10, 40)]),
    ("10 reps at 40kg", None, [E(10, 40)]),
    ("40 for 10", None, [E(10, 40)]),
    ("10 w/ 40", None, [E(10, 40)]),
    ("10x40 failure", None, [E(10, 40, 0)]),
    ("10x40 to failure", None, [E(10, 40, 0)]),
    ("12 bodyweight", None, [E(12, None, bw=True)]),
    ("12 bw", None, [E(12, None, bw=True)]),
    ("12 +10", None, [E(12, 10, bw=True, added=10)]),
    ("10x90 lbs", None, [E(10, 41)]),
    ("10x90 pounds", None, [E(10, 41)]),
    ("10x22.5 rir 2", None, [E(10, 22.5, 2)]),
    ("10x40 and 8x40", None, [E(10, 40), E(8, 40)]),
    ("3 sets of 10 at 40", None, [E(10, 40)] * 3),
    ("3 sets of 10", T40, [E(10, 40)] * 3),
    ("3 series de 10 con 40", None, [E(10, 40)] * 3),
    ("45 seconds", TDUR, [E(dur=45)]),
    ("1 minute", TDUR, [E(dur=60)]),
])
def test_parser_english(text, target, expected):
    res = parse_set_log(text, target)
    assert res.ok, (text, res.reason)
    assert res.entries == expected


def test_describe_in_english():
    set_lang("en")
    assert describe(E(10, 22.5), "per_hand") == "10 reps × 22.5 kg each"
    assert describe(E(12, 10, bw=True, added=10)) == "12 reps + 10 kg added"
    assert describe(E(12, None, bw=True), "bodyweight") == "12 reps · bodyweight"
    set_lang("es")
    assert describe(E(10, 22.5), "per_hand") == "10 reps × 22,5 kg c/u"


# ── Training notes and coach ────────────────────────────────────────────────


def test_progression_notes_follow_language():
    meta = {"load_kind": "total", "equipment_tag": "barbell", "primary_muscle": "chest"}
    hist = [progression.SessionPerf(None, [progression.SetPerf(12, 60, 2)] * 3)]
    set_lang("en")
    t = progression.next_target(hist, 8, 12, 2, meta, {"barbell_step_kg": 2.5})
    assert t.note.startswith("Up to 62.5 kg") and "every set" in t.note
    assert progression.next_target([], 8, 12, 2, meta, {}).note.startswith("Calibration")
    set_lang("es")
    t = progression.next_target(hist, 8, 12, 2, meta, {"barbell_step_kg": 2.5})
    assert t.note.startswith("Subimos a 62,5 kg")


def test_label_dicts_have_english():
    cat = en_catalog()
    labels = [*cards.PR_ES.values(), *session_flow.PLAN_STATUS_ES.values(), progression.CALIBRATION_NOTE]
    missing = [v for v in labels if v not in cat]
    assert not missing, missing


def test_coach_prompt_per_language():
    set_lang("en")
    en = agent._system_blocks()
    set_lang("es")
    es = agent._system_blocks()
    assert en[0]["text"] == SYSTEM_PROMPTS["en"] and es[0]["text"] == SYSTEM_PROMPTS["es"]
    assert "in English" in en[0]["text"] and "en español" in es[0]["text"]
    assert en[0]["cache_control"] == es[0]["cache_control"]


async def test_fake_llm_in_english(world):
    set_lang("en")
    fake = llm_mod.FakeLLM()
    resp = await fake.chat("t", model="fake", messages=[{"role": "user", "content": "hi"}])
    assert resp.content[0].text == "(simulated coach) Got: hi"
    assert _("No hay sesión activa.") == "No active session."
