# Credits, licenses and references

Fit-OS's own code is released under the [MIT License](LICENSE). This page lists everything it builds on or
borrows from, under what terms, and the sources behind its numbers.

## Exercise data and media

| What | Source | License / terms | How Fit-OS uses it |
|---|---|---|---|
| Exercise records: names, muscles, equipment, instructions in 10 languages | [hasaneyldrm/exercises-dataset](https://github.com/hasaneyldrm/exercises-dataset), © 2026 Hasan Emir Yıldırım | MIT (code, dataset structure and instruction text only) | Downloaded at install time by `fitos catalog fetch`, at the commit pinned in `catalog/source.lock`. Not stored in this repository |
| Exercise animations and thumbnails | © **Gym visual** — https://gymvisual.com/ | **Not** MIT. Included in the dataset with the rights holder's written permission; governed by [Gym visual's Terms & Conditions](https://gymvisual.com/content/3-terms-and-conditions-of-use) and the dataset's `NOTICE.md`. Cloning the dataset grants no license to the media | Downloaded with the dataset and converted locally (GIF → MP4) for your own private use. Never committed here. Attribution is shown in the Exercises tab. **If you deploy Fit-OS for anyone but yourself, get your own license from Gym visual or switch to an openly licensed source** |
| `catalog/enrichment.json` | Fit-OS | MIT | Our own Spanish names, muscle taxonomy, movement patterns and equipment requirements, derived from the dataset records |
| Alternative with no media restrictions | [yuhonas/free-exercise-db](https://github.com/yuhonas/free-exercise-db) | Unlicense | Evaluated in [research](docs/research.md); not used today |

## Software

Bundled in this repository (`app/fitos/web/static/vendor/`, license texts in `vendor/LICENSES/`):

| Component | Version | License |
|---|---|---|
| [htmx](https://htmx.org/) | 2.0.4 | BSD Zero-Clause |
| [Apache ECharts](https://echarts.apache.org/) | 5.6.0 | Apache-2.0 (with its `NOTICE`) |
| [Tailwind CSS](https://tailwindcss.com/) browser build | 4.0.17 | MIT, © Tailwind Labs, Inc. |

Installed when the image is built (not redistributed here):

| Component | License |
|---|---|
| [FastAPI](https://fastapi.tiangolo.com/) | MIT |
| [Starlette](https://www.starlette.io/), [Uvicorn](https://www.uvicorn.org/), [HTTPX](https://www.python-httpx.org/) | BSD-3-Clause |
| [Jinja2](https://jinja.palletsprojects.com/) | BSD-3-Clause |
| [SQLAlchemy](https://www.sqlalchemy.org/), [Alembic](https://alembic.sqlalchemy.org/) | MIT |
| [Pydantic](https://docs.pydantic.dev/), pydantic-settings | MIT |
| [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python) | MIT |
| [PyYAML](https://pyyaml.org/) | MIT |
| [python-multipart](https://github.com/Kludex/python-multipart) | Apache-2.0 |
| [python-dateutil](https://github.com/dateutil/dateutil) | Apache-2.0 / BSD-3-Clause |
| [cryptography](https://cryptography.io/) | Apache-2.0 / BSD-3-Clause |
| [SQLite](https://sqlite.org/) | Public domain |
| [FFmpeg](https://ffmpeg.org/) (Debian package in the image) | LGPL-2.1+ / GPL depending on build |

Services Fit-OS runs next to it or talks to:

| Component | License / terms |
|---|---|
| [WAHA](https://waha.devlike.pro/) (WhatsApp HTTP API), devlikeapro | Apache-2.0 (Core edition) |
| [Caddy](https://caddyserver.com/) | Apache-2.0 |
| [age](https://age-encryption.org/) (backup encryption) | BSD-3-Clause |
| [Claude API](https://www.anthropic.com/api) (optional coach) | Anthropic's commercial terms; you bring your own key |

**WhatsApp.** WhatsApp is a trademark of Meta Platforms, Inc. Fit-OS is not affiliated with or endorsed by
Meta or WhatsApp. WAHA connects as a linked device through an unofficial client; WhatsApp's terms don't
allow automated or unofficial clients and may restrict numbers that use them. Fit-OS only writes in one
group you own, at a low rate, but the risk is yours.

## Training science

The training numbers follow published methods; the specific thresholds are summarized in
[research](docs/research.md) §4.

- **Estimated 1RM.** Epley B. *Poundage Chart*. Boyd Epley Workout, 1985. Brzycki M. Strength testing:
  predicting a one-rep max from reps-to-fatigue. *JOPERD* 1993;64(1):88–90.
- **Reps in reserve (RIR) and autoregulation.** Zourdos MC et al. Novel resistance training–specific rating
  of perceived exertion scale measuring repetitions in reserve. *J Strength Cond Res* 2016;30(1):267–275.
  Helms ER, Cronin J, Storey A, Zourdos MC. Application of the repetitions in reserve-based RPE scale for
  resistance training. *Strength Cond J* 2016;38(4):42–49.
- **Weekly volume per muscle.** Schoenfeld BJ, Ogborn D, Krieger JW. Dose-response relationship between
  weekly resistance training volume and increases in muscle mass: a systematic review and meta-analysis.
  *J Sports Sci* 2017;35(11):1073–1082.
- **Training frequency.** Schoenfeld BJ, Ogborn D, Krieger JW. Effects of resistance training frequency on
  measures of muscle hypertrophy: a systematic review and meta-analysis. *Sports Med* 2016;46(11):1689–1697.
- **Session load, monotony and strain.** Foster C et al. A new approach to monitoring exercise training.
  *J Strength Cond Res* 2001;15(1):109–115.
- **Acute:chronic workload ratio** (soft flag only). Gabbett TJ. The training–injury prevention paradox.
  *Br J Sports Med* 2016;50(5):273–280. Williams S et al. Better way to determine the acute:chronic workload
  ratio? *Br J Sports Med* 2017;51(3):209–210.

Double progression, rounding to available loads and the substitution ranking are Fit-OS's own heuristics
built on those ideas; they are not validated clinical tools.

## Laboratory reference ranges

`catalog/biomarkers.yaml` records a `source` for every range. They are starting points, not medical advice:
the range printed by your laboratory and your doctor's judgment come first.

- **Lipids.** National Cholesterol Education Program (NCEP) Expert Panel. Third Report on Detection,
  Evaluation, and Treatment of High Blood Cholesterol in Adults (Adult Treatment Panel III). *Circulation*
  2002;106:3143–3421. Mach F et al. 2019 ESC/EAS Guidelines for the management of dyslipidaemias. *Eur Heart J*
  2020;41:111–188 (ApoB).
- **Glucose and HbA1c.** American Diabetes Association. Standards of Care in Diabetes—2024. *Diabetes Care*
  2024;47(Suppl 1). IADPSG Consensus Panel. Recommendations on the diagnosis and classification of
  hyperglycemia in pregnancy. *Diabetes Care* 2010;33(3):676–682 (1-hour OGTT threshold).
- **Vitamin D.** Holick MF et al. Evaluation, treatment, and prevention of vitamin D deficiency: an Endocrine
  Society clinical practice guideline. *J Clin Endocrinol Metab* 2011;96(7):1911–1930.
- **hs-CRP.** Pearson TA et al. Markers of inflammation and cardiovascular disease: application to clinical
  and public health practice (AHA/CDC). *Circulation* 2003;107:499–511.
- **Assay reference intervals.** Roche Diagnostics method sheets (cobas / Elecsys): ALTL, ASTL, CREJ2,
  Cystatin C Gen.2, UA2, Homocysteine, Elecsys Insulin, Testosterone II, Estradiol III, FSH, LH, TSH,
  Ferritin, Vitamin B12 II.
- **Fibrinogen.** Usual clinical range for the Clauss method.

## Inspiration

The progress tab borrows the idea of daily load and recovery rings from wearables such as WHOOP; no code,
design assets or data from them are used.
