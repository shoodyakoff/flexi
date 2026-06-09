# AGENTS.md — operating rules for Flexi

For code, follow DRY, KISS, YAGNI. Interview the user for missing information,
dilemmas, or ambiguous decisions; offer recommendations as answer options
rather than guessing.

The primary user is a Russian-speaking non-developer. Communicate setup,
onboarding, and asset-placement guidance in Russian, simply, and do not ask them
to run install commands — offer to do it and do it yourself. On a fresh clone, or
when the user is new / stuck on setup / asks "с чего начать" / "что делать", run
`python3 check_setup.py` (readiness check: system deps, Python env, keys, media
inventory, and which routes are buildable), then set things up for them: create
`.venv` with a wheel-stable Python (3.12/3.13; the newest Python may lack ML
wheels), `pip install -r requirements.txt`, and `brew install` any missing system
dep (ffmpeg) after asking. Report readiness simply and point them to
`НАЧНИ_ЗДЕСЬ.md`.

At the start of a coding or content-production task, check `git status --short`.
If there are unrelated or unclear changes, do not mix them into the new task;
summarize the situation and recommend a commit, stash, or separate follow-up.

Do not delete tracked documentation or production instructions unless the user
explicitly asks for that exact deletion. If any instruction references a missing
file, stop and report the broken reference before continuing.

Treat `assets/` media, `output/`, `raw/`, `data/*.sqlite`, `.venv/`,
`.pytest_cache/`, `__pycache__/`, and `.DS_Store` as local runtime artifacts. Do
not treat generated renders or cache files as source of truth. `config.yaml` is
the single source of truth for render / TTS / subtitle / audio defaults — change
behavior there, not by hardcoding.

**Output layout — one folder per video, versions inside.** Every render route
writes to `output/<slug>/v<N>/` (auto-incrementing) and refreshes an
`output/<slug>/latest` symlink to the newest version — re-rendering the same
video never spawns sibling folders (`video1`, `video1_v2`, `video1_auto`…). Pass
`--version vN` to overwrite a specific version instead of creating a new one.
This is centralized in `src/output_paths.py` (`versioned_dir`, `latest_version_dir`,
`seed_reusable`); reuse it from any new route rather than re-deriving output paths.
Expensive input-determined caches (Whisper transcripts, TTS audio) are seeded
from the previous version so re-renders skip recompute.

During long-running builds or renders, start the job and let it run without
step-by-step progress messages unless the user explicitly asks for status.

When creating or editing video scripts, read `VOICEOVER_STYLE.md` first and apply
its `{{pause:...}}` and `{{slow}}...{{/slow}}` conventions to `voiceover_text`
before saving. Add recurring pronunciation fixes to `assets/pronunciation.yaml`
instead of patching one JSON file repeatedly.

At the start of any task that needs media, first process the `raw/` inbox. The
user drops raw footage, music, SFX, ready hook/CTA clips, overlay icons, or a
script JSON into `raw/` (unsorted) and then describes the task. Inventory `raw/`
(`ffprobe` each clip), classify every file to its destination (the mapping table
in `CLAUDE.md` → "The `raw/` inbox"), and show the plan. **Move** — don't copy —
each file into the correct `assets/` subfolder (or `scripts/`), renaming to that
folder's convention, then re-index (`scan-broll` for b-roll; `_meta.json` entries
for hooks/ctas; `config.yaml` wiring for music/SFX). If a file is ambiguous or
two files both fit one slot, ask before moving. Leave only unclassifiable files
in `raw/`, and tell the user what you left and why.

Before creating or building a reel, choose the production route explicitly (see
`CLAUDE.md` and `docs/modes.md`). The five routes, with the Russian names the user
will use: **Broll-рилс с ElevenLabs** (standard/library), **Говорящая голова**
(talking-head clean), **Демо продукта** (ref-style directed — WIP), **Много
рилсов** (Reel Matrix — WIP), and **Динамичный рилс** (3-strip). Map the user's
request to a route by the "User says (RU)" trigger phrases in `CLAUDE.md`. Use the
defaults in `config.yaml` (TTS speed/model, subtitle style, music, b-roll
strategy) without re-asking, unless the user requested something different. If media-to-script matching is
ambiguous or a required asset is missing, ask before rendering.

For the 3-strip and talking-head routes, remember that **iPhone rotation tags
lie** — verify clip orientation by eye (on a frame with a horizon), not by
metadata. After a render, run the matching QA check and report the output path
plus any QA FAILs.

After tests pass, a render succeeds, or a coherent documentation cleanup is
complete, remind the user to make a small, focused commit before starting
unrelated work.
