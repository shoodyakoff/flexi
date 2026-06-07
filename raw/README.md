# raw/ — your drop inbox

This is where **you** put files; the agent sorts them.

Drop anything a task needs here, unsorted — talking-head footage, b-roll, a
ready hook or CTA clip, music, SFX, overlay icons (PNG), or a `VideoScript`
JSON. Then just tell the agent what to make ("собери рилс из этого", "make a
3-strip from today's clips", …).

When you give a media task, the agent will:

1. inventory this folder (`ffprobe` each clip),
2. classify every file and show you the plan,
3. **move** each file into the right place — `assets/broll/`, `assets/hooks/`,
   `assets/music/`, `scripts/`, … — asking first if something is ambiguous,
4. re-index so the pipelines can use it.

So whatever is **still sitting here = not yet processed**. An empty `raw/`
means everything has been filed.

The full destination map lives in `CLAUDE.md` → "The `raw/` inbox".

> Nothing here is tracked by git except this README — `raw/` is local scratch
> input, not source of truth. Use only media you have the rights to.
