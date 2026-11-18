# Contributing

Thanks for wanting to help. Bug reports, ideas and pull requests are welcome.

## Before you start

- **Bugs:** open an issue with what you did, what you expected, what happened, and the last lines of
  `docker compose logs --tail 200 fitos`. Never attach `.env`, anything from `data/`, lab results, or
  WhatsApp ids and keys.
- **Bigger changes** (a new tab, an integration, a dependency): open an issue first so we agree on the
  approach.

## Set up

```bash
git clone <repo-url> Fit-OS && cd Fit-OS
make venv            # app/.venv with the dev dependencies
make test lint       # pytest (in-memory SQLite, no Docker) and ruff
make dev             # http://localhost:8090 with fake WhatsApp and Claude; /dev/wa simulates the group
make catalog demo    # exercise bank + synthetic data, to see every tab filled
```

## Conventions

- Logic in `service.py` and pure modules; routes and the WhatsApp router only validate, call and render.
- Anything that computes a number (progression, PRs, ranking, lab status) gets a unit test.
- Anything external goes through a class with a fake (`FakeWaha`, `FakeLLM`); tests never call the network.
- Schema changes: `make revision m="..."` (SQLite batch mode) and commit the migration.
- The UI and WhatsApp copy are bilingual: wrap text in `_("Spanish text")` and add the English in
  `app/fitos/i18n/en_*.py` (a test checks it). Commands must work in both languages. Code, docs and commits
  are English. Ruff, line length 110.
- Never put real health data in fixtures, screenshots or commit messages. `fitos demo seed` makes
  synthetic data for screenshots.

[architecture.md](docs/architecture.md) explains the modules and the WhatsApp pipeline.
