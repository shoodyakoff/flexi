# pipelines/

Every video-generation pipeline lives here, in one place. They share the engine
library in `src/` (schemas, transcription, subtitles, assembly). Run each from
the repo root so `src.*` and `pipelines.*` resolve.

| Pipeline | File | One-liner |
|---|---|---|
| **Standard / library** | `src/cli.py` (`python -m src.cli build`) | Hook + TTS body + auto/manual b-roll + CTA + subtitles + music from a `VideoScript` JSON. |
| **Talking-head clean** | `render_talking_head_dynamic_clean.py` (+ `talking_head_retake_planner.py`) | Clean raw talking-head footage: silence/retake removal, HDR→SDR, vertical, optional subtitles. |
| **Ref-style directed** | `render_ref_style_directed.py` | Semantic director maps a transcript to 5 visual formats with product b-roll, captions, music. |
| **Reel Matrix** | `reel_matrix.py` | Mix interchangeable hook × tip-order × cta blocks into many unique videos. |
| **3-strip** | `three_strip/build_3strip.zsh` | Three horizontal clips stacked in 9:16, asynchronous cascade. Config-driven. |
| **QA (ref-style)** | `qa_ref_style.py` | Gate a finished video: spec, dead air, HDR wash, captions, loudness. |

Each Python entry point supports `--help`. See `../docs/modes.md` for how the
stages chain and `../CLAUDE.md` for when to use which route.

## Quick start per pipeline

```bash
# Standard / library
python -m src.cli build scripts/example.json

# Talking-head clean
python pipelines/render_talking_head_dynamic_clean.py --help

# Ref-style directed
python pipelines/render_ref_style_directed.py --help

# Reel Matrix (drop clips into raw/ first)
python pipelines/reel_matrix.py ingest --raw raw/
python pipelines/reel_matrix.py dryrun --dir output/matrix

# 3-strip (copy episodes/example.conf -> episodes/<day>.conf, then)
zsh pipelines/three_strip/build_3strip.zsh pipelines/three_strip/episodes/example.conf

# QA a finished video
python pipelines/qa_ref_style.py --final output/<slug>/final.mp4
```
