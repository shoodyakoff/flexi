# Production modes

One page: what each mode does, how they chain, and the commands.

## How the stages chain

```
raw camera clips
      │
      ▼
[clean]  talking_head_dynamic_clean        per-clip cleanup:
         (or reel_matrix clean_clip)        silence-cut · retake removal ·
      │                                      HDR→SDR tonemap · vertical 1080×1920 · transcript
      ▼
[montage] custom_graphics (ref-style)       semantic director → 5 formats ·
      │                                      product b-roll inserts · captions · music
      ▼
[orchestrate] Reel Matrix (optional)        mix hook × tip-order × cta → many unique videos
      │
      ▼
[gate] QA layer                             dead air · HDR wash · spec · plan · audio
      │
      ▼
   final .mp4 (1080×1920, 30fps, bt709)
```

A single video can stop after **montage**. The **Reel Matrix** wraps clean+montage to
generate combinations. The **3-strip** route is a parallel montage style (three
horizontal clips stacked in one frame) that goes straight from raw clips to a
final. **QA** runs on any final.

## Modes

| Mode / route | What it does | Tool |
|---|---|---|
| **`talking_head_dynamic_clean`** | Clean raw talking-head footage: silence-cut, **retake/duble removal**, HDR tonemap, vertical normalize, optional subtitles. | `pipelines/render_talking_head_dynamic_clean.py` + `pipelines/talking_head_retake_planner.py` |
| **`custom_graphics`** (ref-style **dynamic talking head**) | The "монтаж как ref": a semantic director maps the transcript to 5 visual formats, inserts product b-roll, burns captions, mixes music. | `pipelines/render_ref_style_directed.py` |
| **Reel Matrix** | Modular combinatorial composer on top of `custom_graphics`: shoot interchangeable blocks, auto-classify, mix hook×tip-order×cta into many unique videos. | `pipelines/reel_matrix.py` |
| **Standard / library** | The base pipeline: ready hook + TTS body + auto/manual b-roll + CTA + subtitles + music from a `VideoScript` JSON. | `src/cli.py` (`python -m src.cli build`) |
| **3-strip** | Three horizontal clips stacked in one 9:16 frame, asynchronous cascade. Config-driven (one `.conf` per episode). | `pipelines/three_strip/build_3strip.zsh` |
| **QA layer** | Gate a finished video against known failure modes. Not a mode — a check. | `pipelines/qa_ref_style.py` · `pipelines/three_strip/qa.py` |

> **`library_dynamic`** (head-less, product-first) was only a **draft** — **not implemented**.

## The 5 ref-style (`custom_graphics`) formats

1. `format_1_hook_metal` — emotional/problem hook (metal-sticker lettering, lower third)
2. `format_2_framed_face` — calm explanation inside the yellow frame
3. `format_3_turn_badge` — reveal / turn / proof
4. `format_4_blue_demo` — product instruction/demo on the blue grid (big readable card)
5. `format_5_lower_demo_cta` — closing CTA/link with a lower-third product demo

Guardrails: semantic (not 1-2-3-4-5 rotation); no more than two identical formats in a
row (a long demo run breaks back to the head); head cutaways punched into long demos.

## Reel Matrix — workflow

```bash
# 0. Drop all clips (hooks, tips, ctas — any names) into raw/

# 1. Clean + classify every clip -> output/matrix/cleaned/ + output/matrix/manifest.json
python pipelines/reel_matrix.py ingest --raw raw/

# 2. Review output/matrix/manifest.json — fix any wrong `role` (classifier is a guess).
#    Optionally set per-block product inserts (see below).

# 3. See the combination matrix (no render)
python pipelines/reel_matrix.py dryrun --dir output/matrix

# 4. Render one combo (ordered block keys)
python pipelines/reel_matrix.py build --dir output/matrix \
    --combo h2,t1,t2,t3,c2 --output output/matrix/final/h2_c2.mp4
```

Filenames containing `hook`/`tip`/`cta` (or `h1`/`t2`/`c3`) override the content guess.

### Output layout (`output/matrix/`)
- `cleaned/` — one cleaned+transcribed clip per block (reused across combos)
- `work/` — all assembly + render intermediates (base / ass / plan / diagnostics)
- `final/` — **production videos only** — just the finished `.mp4`s, nothing else

### Manifest per-block fields (edit by hand after ingest)
- `role` — `hook` / `tip` / `cta` (fix any misclassification here)
- `product` — product b-roll clip to insert on this block, e.g. `sa_2_demo.mp4`
- `product_from` — delay the demo until this spoken word, e.g. `пиши`
  (the talking head shows first, the demo appears when the voice reaches the word)

Product b-roll tags live in `assets/broll_brand/demo_tags.json`
(`{clip.mp4: [keyword, ...]}`) and are matched to what is said on each demo beat.

## 3-strip — workflow

Three horizontal clips stacked in one 9:16 frame (~632px bands), with an
**asynchronous cascade**: the middle band leads, top enters +0.5s, bottom +1.0s,
then each band cuts on its own rhythm. The engine is config-driven — one `.conf`
per episode, the engine and QA are reusable. Full ruleset: `pipelines/three_strip/RULES.md`.

```bash
# 1. (optional) understand a long clip while authoring the edit
RAW=raw/myday zsh pipelines/three_strip/analyze.zsh 4907   # -> output/analyze/4907/sheet.png

# 2. copy the template and author MID/TOP/BOT in it (index-aligned columns)
cp pipelines/three_strip/episodes/example.conf pipelines/three_strip/episodes/myday.conf

# 3. build (renders the cascade + bed outro + music, then auto-runs QA)
zsh pipelines/three_strip/build_3strip.zsh pipelines/three_strip/episodes/myday.conf
```

Key rules (see `RULES.md`): HDR→SDR tonemap; main action centered (center-crop);
source audio off; never 3 lookalike clips in one trio; iPhone rotation tags lie
(verify by eye, list flips in `NEED180`); bed bookend (open & close on the same
calm shot). `three_strip/qa.py` checks format, no-3-same (phash≥10), dead bands,
and face gaps, writing `qa_report.txt` + an annotated `qa_sheet.png`.

## QA

```bash
python pipelines/qa_ref_style.py --final output/matrix/final/h2_c2.mp4 --source raw/IMG_4804.MOV
```

Checks: container spec (1080×1920 / 30fps / audio / bt709), **dead air** (FAIL > 0.9s),
black frames, edit-plan sanity (known formats, ≤2 same in a row, coverage),
subtitle ASR junk, product b-roll connected, loudness + true-peak, and an **HDR
source → tonemap** check with an extracted frame for manual color review.
Exit code = number of FAILs.
