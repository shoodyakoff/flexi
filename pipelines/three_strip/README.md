# 3-Strip Reel Pipeline

Reproducible montage of a day of phone footage into a vertical 9:16 "3-strip"
reel (three horizontal clips stacked, asynchronous cascade). One config per
episode; the engine + QA are reusable. Full ruleset in [RULES.md](RULES.md).

```
three_strip/
  build_3strip.zsh     # ENGINE — config-driven, never edit per episode
  qa.py                # QA layer — screenshots + auto-checks acceptance criteria
  analyze.zsh          # helper — dense timestamped contact sheet for a long clip
  RULES.md             # the montage ruleset (source of truth)
  episodes/
    example.conf       # an episode = the only file you author/edit (copy this)
```

## Run
```sh
# 1) understand a long clip (optional, while authoring the edit):
RAW=raw/myday zsh analyze.zsh 4907   # -> output/analyze/4907/sheet.png + scene cuts

# 2) build the reel from an episode config (renders + auto-runs QA):
zsh build_3strip.zsh episodes/example.conf
#    -> <OUT>/<NAME>.mp4 + qa_report.txt + qa_sheet.png
```
Tiles are cached per (clip,in,dur); re-running after a small edit takes seconds.

## Workflow for a NEW day
1. **Ingest**: drop clips in `raw/<day>/`. Copy `episodes/example.conf` → `<day>.conf`,
   set `NAME/RAW/OUT/MUSIC`.
2. **Diff**: list clips; note any the user deleted (don't use) and new ones.
3. **Orient**: for every clip, verify upright by eye (RAW vs auto — tags lie).
   POV clips: check a frame with a horizon, not the midpoint. Collect 180-flips
   into `NEED180`.
4. **Analyze** long clips (`analyze.zsh <num>`); pick 1+ fragments (demo→usage,
   reveal, key moment).
5. **Author** the edit in the conf: fill `MID/TOP/BOT` chronologically,
   index-aligned (same DUR per column), each clip once, faces rare, demo→usage
   adjacent in one band, max 1 screen per column, never 3 lookalikes per column.
   Set the bed `OUTRO_*`.
6. **Build** → engine renders + runs QA.
7. **QA loop**: read `qa_report.txt`; if a check FAILs, fix that column in the
   conf and rebuild (cached → fast). Eyeball `qa_sheet.png` (🟥 dup, 🟩 face)
   for orientation/centring/colour. Ship only at all-PASS.

## Config keys (episode)
| key | meaning |
|-----|---------|
| `NAME/RAW/OUT` | episode id, source folder, output folder |
| `WORK` | tile cache dir (default `$OUT/work_$NAME`) |
| `MAX_DUR` | length cap (45) |
| `NEED180` | space list of clips to rotate 180° |
| `MUSIC/MUSIC_START/MUSIC_VOL` | track, start offset (s), volume |
| `LEAD_top/LEAD_bot` | cascade offsets (0.5 / 1.0) |
| `MID/TOP/BOT` | per-band timelines, entries `"IMGNUM IN DUR"`, index-aligned |
| `ENDLEN`+`OUTRO_TOP/BOT/CTR`(+`_VIS`) | bed cascade-out outro (0 = none) |

## Defaults (confirmed)
- Length ≤ **45s**. Faces **rare**. **No repeats** (each clip once).
- 3-strip cascade; HDR→SDR tonemap; source audio off; bed bookend (start & end in bed).
- "faces often / clip reuse" is the alternate mode — just add more face entries / repeat clips in the conf.

## Acceptance criteria (auto-checked by qa.py)
format 1080×1920·30fps·audio · duration≤MAX · **no-3-same** (phash≥10) ·
face-gap (rare-mode: informational) · no dead band (outside intro/outro).
Visual (qa_sheet): orientation upright, action centred, colour, bed finale.
