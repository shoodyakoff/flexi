# Flexi

**An agent-driven pipeline for producing vertical short-form video** (9:16,
1080×1920, 30fps) — subtitles, TTS voiceover, b-roll assembly, talking-head
cleanup, ref-style montage, combinatorial reels, and a 3-strip format. You drive
it from the command line (and it's designed to be driven by a coding agent like
Claude Code, which self-onboards from `CLAUDE.md`).

> Flexi is the reusable engine. It ships with **no media** — bring your own
> footage, music, and SFX. Fonts are bundled under open licenses.

## Pipelines

| Pipeline | What it does |
|---|---|
| **Standard / library** | Ready hook + TTS voiceover + auto/manual b-roll + CTA + subtitles + music, from a small `VideoScript` JSON. |
| **Talking-head clean** | Clean raw talking-head footage: silence/retake removal, HDR→SDR, vertical, optional subtitles. |
| **Ref-style directed** | A semantic director maps a transcript to 5 visual formats with product b-roll, burned captions, music. |
| **Reel Matrix** | Mix interchangeable hook × tip-order × cta blocks into many unique videos. |
| **3-strip** | Three horizontal clips stacked in one 9:16 frame, asynchronous cascade. |

Plus a **QA layer** that gates finished videos (spec, dead air, HDR wash,
captions, loudness). See `docs/modes.md` for how the stages chain.

## Requirements

- **ffmpeg** (with libass) and **ffprobe**
- **Python** ≥ 3.10
- **ElevenLabs API key** — only for TTS voiceover ([elevenlabs.io](https://elevenlabs.io))
- **zsh** — only for the 3-strip pipeline

## Install

```bash
git clone <your-fork-url> flexi && cd flexi
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # then add your ELEVENLABS_API_KEY
.venv/bin/python -m pytest    # integration tests needing media auto-skip
```

## Usage

```bash
# Standard / library — build a reel from a VideoScript JSON
.venv/bin/python -m src.cli build scripts/example.json
.venv/bin/python -m src.cli validate scripts/example.json
.venv/bin/python -m src.cli --help          # all subcommands

# Talking-head clean / Ref-style / Reel Matrix
.venv/bin/python pipelines/render_talking_head_dynamic_clean.py --help
.venv/bin/python pipelines/render_ref_style_directed.py --help
.venv/bin/python pipelines/reel_matrix.py --help

# 3-strip (config-driven)
cp pipelines/three_strip/episodes/example.conf pipelines/three_strip/episodes/myday.conf
zsh pipelines/three_strip/build_3strip.zsh pipelines/three_strip/episodes/myday.conf
```

Or with the Makefile: `make test`, `make validate SCRIPT=…`, `make build SCRIPT=…`.

> `scripts/example.json` shows the `VideoScript` format. `validate` and `build` resolve `hook_id` / `cta_id` / b-roll from `assets/*/_meta.json`, so register your own hook / CTA / b-roll clips there first (see `assets/README.md`).

## Configuration

`config.yaml` is the single source of truth for resolution, codecs, audio
levels, TTS model/speed, subtitle styles, rhythm, b-roll rotation, and the hook
montage. Edit behavior there rather than hardcoding.

Add your media under `assets/` (see `assets/README.md` for the layout). Fonts in
`assets/fonts/` are bundled (open-licensed); the defaults render subtitles out of
the box. To use commercial fonts you own (e.g. Gilroy, Druk Wide), drop them in
and point `config.yaml` at the family name — see `assets/fonts/README.md`.

## Driving it with an agent

This repo is structured to self-onboard a coding agent: `CLAUDE.md` is read on
launch and indexes every pipeline, setup step, and operating rule; `AGENTS.md`
holds the operating contract; and `.claude/skills/video-montage/` is a
step-by-step ffmpeg/TTS/subtitle skill. Clone, open in your agent, and ask it to
build a reel.

## Project structure

```
src/         shared engine library + `src.cli`
pipelines/   every pipeline entry point (incl. three_strip/)
docs/        modes.md + agentic-mode guides
tests/       pytest suite (integration tests auto-skip without media)
assets/      bundled fonts + your media (you supply)
scripts/     VideoScript JSON inputs (example.json)
config.yaml  all render/TTS/subtitle/audio knobs
```

## License

MIT — see [LICENSE](LICENSE). Bundled fonts are under the SIL Open Font License
(see `assets/fonts/`).
