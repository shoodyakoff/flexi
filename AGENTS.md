# AGENTS.md — operating rules for Flexi

For code, follow DRY, KISS, YAGNI. Interview the user for missing information,
dilemmas, or ambiguous decisions; offer recommendations as answer options
rather than guessing.

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

During long-running builds or renders, start the job and let it run without
step-by-step progress messages unless the user explicitly asks for status.

When creating or editing video scripts, read `VOICEOVER_STYLE.md` first and apply
its `{{pause:...}}` and `{{slow}}...{{/slow}}` conventions to `voiceover_text`
before saving. Add recurring pronunciation fixes to `assets/pronunciation.yaml`
instead of patching one JSON file repeatedly.

Before creating or building a reel, choose the production route explicitly (see
`CLAUDE.md` and `docs/modes.md`): standard/library, talking-head clean,
ref-style directed, Reel Matrix, or 3-strip. Use the defaults in `config.yaml`
(TTS speed/model, subtitle style, music, b-roll strategy) without re-asking,
unless the user requested something different. If media-to-script matching is
ambiguous or a required asset is missing, ask before rendering.

For the 3-strip and talking-head routes, remember that **iPhone rotation tags
lie** — verify clip orientation by eye (on a frame with a horizon), not by
metadata. After a render, run the matching QA check and report the output path
plus any QA FAILs.

After tests pass, a render succeeds, or a coherent documentation cleanup is
complete, remind the user to make a small, focused commit before starting
unrelated work.
