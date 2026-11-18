# Development

```bash
make venv          # once: app/.venv with dev dependencies
make test lint     # pytest (in-memory SQLite) + ruff; no Docker needed
make dev           # http://localhost:8090, code mounted with --reload, fake WhatsApp and Claude
make catalog demo  # exercise bank + synthetic data
make logs | shell | down
```

## The WhatsApp simulator

http://localhost:8090/dev/wa (development only) is the group: what you type goes through the real webhook
pipeline and router, and what Fit-OS "sends" through `FakeWaha` shows up as bubbles, videos included.

## The coach without an API key

`LLM_MODE=fake` uses `FakeLLM`, which answers with a canned text or a scripted list of responses (see
`tests/test_coach_and_pages.py`). Tests never call the network.

## Migrations

Models are in `app/fitos/core/models.py`. After changing them:

```bash
make revision m="add rest timer to items"    # autogenerates against a fresh SQLite, batch mode
```

Review the generated file, commit it, and CI runs `alembic upgrade head && alembic check`.

## Tests worth knowing

| File | Covers |
|---|---|
| `test_webhook.py` | HMAC, foreign chats dropped before storage, anti-loop guards, idempotency |
| `test_parser.py` | the set grammar (`10x40 r2`, `3x10x40`, pounds, RPE, bodyweight) |
| `test_progression.py`, `test_substitution.py`, `test_planner.py`, `test_metrics.py` | the training numbers |
| `test_labs.py` | ranges by sex and age, CSV import ambiguity rules |
| `test_session_flow.py` | a whole session over WhatsApp |
| `test_auth.py` | Caddy secret, forged headers, peer network |
| `test_demo.py`, `test_coach_and_pages.py` | every tab renders |

## Screenshots and the banner

README images are made from **synthetic data only** (`make demo` on an empty DB), never from a real
database.

- `docs/images/banner.png` is rendered from `docs/images/banner.html`:
  ```bash
  google-chrome --headless=new --hide-scrollbars --window-size=1600,620 \
    --screenshot=docs/images/banner.png "file://$PWD/docs/images/banner.html"
  ```
- Tab screenshots: an empty DB, `fitos catalog import` **without** thumbnails in `$DATA_DIR/catalog/thumbs`
  (the exercise images are © Gym visual and must not end up in the repo), language set to English in
  Settings, `fitos demo seed`, then capture the pages at 1440 px the same way.

## Translations

Spanish is the source language: `_("Sesión de hoy")` in Python and `{{ _("Sesión de hoy") }}` in templates,
with the English in `app/fitos/i18n/en_<area>.py`. `test_i18n.py` lists any literal without English or with
mismatched `{placeholders}`. For counts write both forms (`"{n} marcador"` / `"{n} marcadores"`). WhatsApp
keywords live in sets in `wa/session_flow.py` and `wa/router.py`; add synonyms in both languages.

## Icons and the group picture

`docs/images/icon.html` is the source of the WhatsApp group picture and the app icons (symbol only with `#mark`):

```bash
google-chrome --headless=new --hide-scrollbars --window-size=1024,1024 \
  --screenshot=docs/images/group-icon.png "file://$PWD/docs/images/icon.html"
for n in 180 192 512; do google-chrome --headless=new --hide-scrollbars --window-size=$n,$n \
  --screenshot=app/fitos/web/static/icons/icon-$n.png "file://$PWD/docs/images/icon.html#mark"; done
```
