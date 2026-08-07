# Flexi — agent onboarding

**Flexi is a local, agent-driven pipeline for producing vertical short-form
video (9:16, 1080×1920, 30fps): subtitles, TTS voiceover, b-roll assembly,
talking-head cleanup, ref-style montage, combinatorial reels, and a 3-strip
format.** You (the agent) drive it with `ffmpeg` + Python from the command line.

This file is read on launch. Read it fully before acting, then skim the linked
docs for the pipeline you'll use.

---

## First-run setup (do this once, on a fresh clone)

> Run `python3 check_setup.py` (or `make check`) at any point — it diagnoses
> every item below in plain Russian and prints the exact fix command. For a
> non-developer user, perform the setup yourself and just report when it's ready
> (see "Onboard in Russian" under Operating rules). Human onboarding:
> `НАЧНИ_ЗДЕСЬ.md`.

1. **System deps** — `ffmpeg` (with libass), `python3` ≥ 3.10 (recommended:
   **3.12** — see the version note below). The 3-strip and talking-head
   pipelines also use `ffprobe` (ships with ffmpeg).
2. **Python env**:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
3. **Secrets** — copy `.env.example` → `.env` and fill in `ELEVENLABS_API_KEY`
   (+ optional `ELEVENLABS_VOICE_ID`). Needed only for TTS voiceover.
4. **Verify**:
   ```bash
   .venv/bin/python -m pytest        # integration tests needing media auto-skip
   ```
5. **Media** — you supply your own footage/music/SFX. See `assets/README.md`
   for where each kind goes. Fonts are already bundled (open-licensed).

If a step's tool is missing, tell the user exactly what to install rather than
guessing.

---

## The pipelines (all live under `pipelines/` + the `src.cli` library)

Pick **one** route before building. Full reference: `docs/modes.md`.

The user speaks Russian and refers to routes by the **RU name** below. Map the
request to a route by the **"User says (RU)"** trigger phrases.

| # | Pipeline — RU name | Use when | User says (RU) | Entry point |
|---|---|---|---|---|
| 1 | **Broll-рилс с ElevenLabs** (Standard / library) | Hook + TTS voiceover + auto/manual b-roll + CTA + subtitles + music, assembled from a `VideoScript` JSON. The base route. | «собери Broll-рилс», «сделай рилс из этого текста / сценария», «рилс с озвучкой и вставками» | `python -m src.cli build scripts/<slug>.json` |
| 2 | **Говорящая голова** (Talking-head clean) | You have raw talking-head footage and want a clean dynamic cut (silence/retake removal, HDR→SDR, vertical), optionally subtitled, optionally with an animated title overlaid at the start (see `--title` below). | «почисти это видео / интервью», «убери паузы и дубли», «собери говорящую голову», «сделай вертикальным с субтитрами», «добавь заголовок челленджа» | `python pipelines/render_talking_head_dynamic_clean.py` |
| 3 | **Демо продукта** (Ref-style directed, `custom_graphics`) — *WIP* | Turn a transcript into a directed montage across **5 visual formats** with product b-roll, burned captions, music. | «сделай видео с демо продукта», «собери демо» | `python pipelines/render_ref_style_directed.py` |
| 4 | **Много рилсов** (Reel Matrix) — *WIP* | Shoot interchangeable hook/tip/cta blocks and mix them into many unique videos (combinatorial, on top of #2+#3). | «сделай серию рилсов из файлов», «собери серию рилсов», «нужно много вариантов», «перемешай вступления и концовки» | `python pipelines/reel_matrix.py` |
| 5 | **Динамичный рилс** (3-strip) | Three horizontal clips stacked in one 9:16 frame, asynchronous cascade. Config-driven; great for "a day of footage". | «собери динамичный рилс», «три клипа в одном кадре из сегодняшних видео» | `zsh pipelines/three_strip/build_3strip.zsh episodes/<name>.conf` |
| — | **QA** | Gate a finished video (format, dead air, HDR wash, captions, loudness, duplicate takes). Not a route — a check. | — | `python pipelines/qa_talking_head.py --slug <slug>` (talking-head: per-clip cut timing + duplicate-phrase check) · `python pipelines/qa_ref_style.py` (ref-style) · `three_strip/qa.py` (3-strip) |

Routes **#3 (Демо продукта)** and **#4 (Много рилсов)** are *work-in-progress* —
usable but not yet stable; tell the user so if they pick one.

**Feeding route #2 (Говорящая голова) — clip ingestion rules:**
- A day's footage is usually **several clips** (the person stops/restarts the
  camera). Pass **each clip as its own `--input`**, or point `--clips-dir
  <folder>` at the day folder (it ingests every video inside, sorted, as a
  separate source). **Never `ffmpeg -f concat -c copy` them into one file** —
  iPhone clips in a day often have **mixed rotation** (talking parts `90`,
  screen/demo parts `-90`), and stream-copy concat forces the first clip's
  rotation onto all, flipping the odd ones **upside-down**. The pipeline
  auto-rotates each source independently and logs a per-clip rotation table on
  start; eyeball a frame from each demo segment after rendering.
- **Silent demo/screen clips** (pointing the phone at a laptop, no narration)
  get trimmed away by silence detection. To play one end-to-end, pass
  `--keep-full-source <index>` (0-based, in input order; repeatable) — it skips
  trimming/retakes for that clip and renders it as one un-cropped beat.
- `--retake-mode smart` can over-cut: it sometimes reads a real continuous
  thought as a false start and drops several seconds mid-sentence. The pipeline
  prints a `⚠ retake removal dropped …` warning when this happens — review it,
  and if a real sentence was cut, re-render with `--retake-mode off` or an
  explicit `--edit-decisions-json` plan.
- **Subtitle vocabulary** is auto-corrected from `config.yaml →
  subtitle_corrections` before burning (e.g. `рилз`→`рилс`, `сас`→`SaaS`, plus
  your own product/brand terms). Add recurring Whisper mis-hears there
  instead of hand-editing `subtitles.ass`.

**«Добавь заголовок челленджа»** (talking-head add-on): when the user asks to add a
challenge title, they have an exported **animated title clip** — text on a **black
background**, 1080×1920, text pre-positioned top-left. Sort it into `assets/titles/`
(see the inbox table) and pass it to route #2 via `--title assets/titles/<file>.mp4`.
The pipeline keys out the black background, overlays the title onto the **start** of
the video, and fades it out at its end — producing an extra `final_titled.mp4`
(subtitles stay at the bottom). Keying/fade defaults: `config.yaml → title_overlay`.

**Auto-title for a recurring titled series.** When a talking-head render belongs to a
recurring series that always carries the same opener title, pass `--auto-title` to
overlay `config.yaml → title_overlay.default_title` (currently
`assets/titles/title_001.mp4`) without being asked. An explicit `--title <file>`
overrides it. To change the series title, drop the new clip into `assets/titles/` and
update `title_overlay.default_title` (one line). The titled output is `final_titled.mp4`.

The **5 ref-style formats**: `hook_metal` → `framed_face` → `turn_badge` →
`blue_demo` → `lower_demo_cta` (semantic order, not a fixed rotation).

`src/` is the shared engine library (schemas, assembly, subtitles, tts,
transcribe, b-roll, rhythm, hook montage, ref-style director) that the routes
build on. `python -m src.cli --help` lists all CLI subcommands.

---

## Project map

```
CLAUDE.md            ← you are here (agent onboarding, read on launch)
AGENTS.md            ← operating rules
README.md            ← human quickstart
НАЧНИ_ЗДЕСЬ.md       ← русский онбординг для новичка (read first if the user is RU)
check_setup.py       ← readiness check (deps/env/media) — `python3 check_setup.py`
config.yaml          ← all render/TTS/subtitle/audio knobs (one source of truth)
src/                 ← shared engine library + `src.cli`
pipelines/           ← every pipeline entry point (one place)
  three_strip/       ← 3-strip engine + RULES.md + episodes/example.conf
docs/                ← modes.md (mode reference), agentic-mode.md + usage guide
tests/               ← pytest suite (integration tests auto-skip without media)
raw/                 ← your drop inbox; the agent sorts it into assets/ + scripts/ (git-ignored)
assets/              ← fonts (bundled) + your media (you supply) — see assets/README.md
scripts/             ← VideoScript JSON inputs (example.json shows the format)
.claude/skills/video-montage/  ← deep ffmpeg/TTS/subtitle how-to (a skill)
```

---

## The `raw/` inbox (how you feed the pipelines)

`raw/` (repo root) is the user's drop folder. The user dumps whatever a task
needs — footage, music, SFX, a ready hook/CTA clip, overlay icons, even a
`VideoScript` JSON — into `raw/` (flat, no sorting) and then describes what to
make. **Sorting it is your job.**

At the **start of any task that needs media**, before building:

1. **Inventory** `raw/` — list it and `ffprobe` each clip (duration, resolution,
   orientation, audio track). Filename hints help but aren't authoritative;
   remember iPhone rotation tags lie — confirm orientation by eye when it matters.
2. **Classify & plan** — map each file to its destination (table below) and show
   the user the plan. If a file is ambiguous (could be a hook *or* b-roll) or two
   files both fit one slot, **ask before moving** — don't guess.
3. **File it** — **move** (not copy) each file into its folder, renaming to that
   folder's convention (e.g. `broll_012.mp4`, `hook_003.mp4`). `raw/` should end
   empty except files you flagged as unclassifiable.
4. **Re-index** so the engine sees the new media (last column).

| What's in `raw/` | Move to | Then |
|---|---|---|
| Talking-head footage (person to camera) | `assets/talking_head_sources/` | pass its path to the talking-head / ref-style pipeline |
| General b-roll (scenes, atmosphere) | `assets/broll/` | `python -m src.cli scan-broll` (builds the normalized ingest cache + indexes); descriptions via `annotate-broll` or by editing `broll/_meta.json` |
| Product / screen-demo clips | `assets/broll_brand/` | referenced as the product insert (script / `config.yaml`) |
| Ready hook clip (opener) | `assets/hooks/` | add an entry to `assets/hooks/_meta.json` (or `scan-assets hooks` when a human drives the prompts) |
| Ready CTA clip (ending) | `assets/ctas/` | add an entry to `assets/ctas/_meta.json` (or `scan-assets ctas`) |
| Animated title clip (text on **black** bg, 1080×1920, opener — «заголовок челленджа») | `assets/titles/` | pass its path to the talking-head pipeline via `--title assets/titles/<file>.mp4` |
| Music track | `assets/music/` | point `config.yaml → edit_profile.music_file` at it |
| SFX (riser, swoosh…) | `assets/sounds/` | referenced by name in `config.yaml` |
| Overlay icon (PNG glyph) | `assets/hook_overlays/` | add its keyword(s) to `assets/hook_overlays/_meta.json` |
| `VideoScript` JSON | `scripts/` | build with `python -m src.cli build scripts/<slug>.json` |

`scan-broll` is non-interactive (safe to run); `scan-assets` / `annotate-broll`
prompt interactively, so when running unattended, write the `_meta.json` entry
yourself instead. `raw/` is git-ignored (only its `README.md` is tracked) — it's
local input, never source of truth.

---

## Operating rules (full set in `AGENTS.md`)

- Before building a reel, **choose the route explicitly** (table above) and read
  the relevant doc. For voiceover text, read `VOICEOVER_STYLE.md` and use the
  `{{pause:...}}` / `{{slow}}…{{/slow}}` markup.
- Treat `output/`, `raw/`, `assets/` media, `.venv/`, and caches as local
  runtime artifacts — never source of truth.
- **`raw/` is the inbox.** At the start of a media task, sort it: classify each
  dropped file, show the plan, **move** it into the right `assets/` folder or
  `scripts/` (ask when ambiguous), then re-index. See "The `raw/` inbox" above.
- **Onboard in Russian, and do the setup yourself.** The user is a Russian-
  speaking non-developer — never make them run terminal commands. Offer to install
  everything ("да, могу всё поставить сам — напишу, когда будет готово"), then do
  it: create `.venv` with **Python 3.12** (the wheel-stable target — the newest
  Python may lack prebuilt ML wheels; 3.13 also dropped stdlib `audioop`),
  `pip install -r requirements.txt`, and `brew install`
  any missing system dep (ffmpeg) after asking. Run `python3 check_setup.py`
  before/after and report readiness **simply, in Russian**. Point them to
  `НАЧНИ_ЗДЕСЬ.md`.
- `config.yaml` is the single source of render/TTS/subtitle defaults. Change
  behavior there, not by hardcoding.
- **Renders are versioned: `output/<slug>/v<N>/`** with a `latest` symlink to the
  newest. Re-rendering a video adds a new `vN` in the *same* folder (never a
  sibling folder); pass `--version vN` to overwrite one. Shared logic lives in
  `src/output_paths.py` — reuse it in any new route. Details in `AGENTS.md`.
- **iPhone rotation tags lie** — for the 3-strip / talking-head routes, verify
  clip orientation by eye on a frame with a horizon, not by metadata.
- After a render, run the matching QA check and report the output path + any QA
  FAILs.
- **MANDATORY audio-dedup gate (do this as its own step before ever saying a
  talking-head/ref-style render is "готово"/done).** `qa_talking_head.py` only
  catches *identical* duplicate takes (`ДУБЛИ`); it does **not** catch the speaker
  **restating the same thought in different words** or **stuttering a word twice**
  (e.g. d63: «и токены у меня юзаются меньше…» immediately restated as «и токенов
  я заюзал меньше», and «поэтому, поэтому если…»). So: **transcribe the FINAL
  rendered audio** (`ffmpeg -i final_subtitled.mp4 -vn -ac 1 -ar 16000 …wav` →
  faster-whisper), **read the transcript end-to-end, and flag any back-to-back
  restatement or doubled word.** If found, cut it via an explicit
  `--edit-decisions-json` (drop the weaker/earlier take; keep tails padded — see
  the crossfade note) and re-render. Only report done once the final transcript
  reads clean with no repeated thought. Never skip this because QA said
  `ДУБЛИ: не найдено`.

## When to ask the user

Ask a short question (don't guess) when: the production route is ambiguous; a
required asset/clip is missing or two clips both match; the voice speed/subtitle
style/music should differ from `config.yaml`; or media-to-script matching is
unclear. Otherwise proceed with the documented defaults.

## Deeper references

- `docs/modes.md` — how the stages chain + per-mode commands.
- `pipelines/README.md` — index of pipeline entry points.
- `pipelines/three_strip/RULES.md` — the 3-strip montage ruleset.
- `VIDEO_CREATION_MODES.md` — long-form route contract.
- `.claude/skills/video-montage/SKILL.md` — step-by-step ffmpeg/TTS/subtitle recipes.
