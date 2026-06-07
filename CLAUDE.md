# Flexi — agent onboarding

**Flexi is a local, agent-driven pipeline for producing vertical short-form
video (9:16, 1080×1920, 30fps): subtitles, TTS voiceover, b-roll assembly,
talking-head cleanup, ref-style montage, combinatorial reels, and a 3-strip
format.** You (the agent) drive it with `ffmpeg` + Python from the command line.

This file is read on launch. Read it fully before acting, then skim the linked
docs for the pipeline you'll use.

---

## First-run setup (do this once, on a fresh clone)

1. **System deps** — `ffmpeg` (with libass), `python3` ≥ 3.10. The 3-strip and
   talking-head pipelines also use `ffprobe` (ships with ffmpeg).
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

| # | Pipeline | Use when | Entry point |
|---|---|---|---|
| 1 | **Standard / library** | Hook + TTS voiceover + auto/manual b-roll + CTA + subtitles + music, assembled from a `VideoScript` JSON. The base route. | `python -m src.cli build scripts/<slug>.json` |
| 2 | **Talking-head clean** | You have raw talking-head footage and want a clean dynamic cut (silence/retake removal, HDR→SDR, vertical), optionally subtitled. | `python pipelines/render_talking_head_dynamic_clean.py` |
| 3 | **Ref-style directed** (`custom_graphics`) | Turn a transcript into a directed montage across **5 visual formats** with product b-roll, burned captions, music. | `python pipelines/render_ref_style_directed.py` |
| 4 | **Reel Matrix** | Shoot interchangeable hook/tip/cta blocks and mix them into many unique videos (combinatorial, on top of #2+#3). | `python pipelines/reel_matrix.py` |
| 5 | **3-strip** | Three horizontal clips stacked in one 9:16 frame, asynchronous cascade. Config-driven; great for "a day of footage". | `zsh pipelines/three_strip/build_3strip.zsh episodes/<name>.conf` |
| — | **QA** | Gate a finished video (format, dead air, HDR wash, captions, loudness). Not a route — a check. | `python pipelines/qa_ref_style.py` (ref-style) · `three_strip/qa.py` (3-strip) |

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
config.yaml          ← all render/TTS/subtitle/audio knobs (one source of truth)
src/                 ← shared engine library + `src.cli`
pipelines/           ← every pipeline entry point (one place)
  three_strip/       ← 3-strip engine + RULES.md + episodes/example.conf
docs/                ← modes.md (mode reference), agentic-mode.md + usage guide
tests/               ← pytest suite (integration tests auto-skip without media)
assets/              ← fonts (bundled) + your media (you supply) — see assets/README.md
scripts/             ← VideoScript JSON inputs (example.json shows the format)
.claude/skills/video-montage/  ← deep ffmpeg/TTS/subtitle how-to (a skill)
```

---

## Operating rules (full set in `AGENTS.md`)

- Before building a reel, **choose the route explicitly** (table above) and read
  the relevant doc. For voiceover text, read `VOICEOVER_STYLE.md` and use the
  `{{pause:...}}` / `{{slow}}…{{/slow}}` markup.
- Treat `output/`, `raw/`, `assets/` media, `.venv/`, and caches as local
  runtime artifacts — never source of truth.
- `config.yaml` is the single source of render/TTS/subtitle defaults. Change
  behavior there, not by hardcoding.
- **iPhone rotation tags lie** — for the 3-strip / talking-head routes, verify
  clip orientation by eye on a frame with a horizon, not by metadata.
- After a render, run the matching QA check and report the output path + any QA
  FAILs.

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
