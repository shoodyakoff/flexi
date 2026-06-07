# assets/

Engine assets. **Media is not shipped** — you supply your own footage, music,
and sound effects. Only text/config assets are tracked in git (fonts under an
open license, the pronunciation map, and the overlay keyword map). The media
folders below ship as empty skeletons (`.gitkeep`) so you know where to drop
files; anything you add is git-ignored automatically.

| folder | what goes here | tracked? |
|---|---|---|
| `fonts/` | subtitle/heading fonts (open-licensed set bundled — see `fonts/README.md`) | ✅ yes |
| `pronunciation.yaml` | word → phonetic overrides for TTS | ✅ yes |
| `hook_overlays/_meta.json` | overlay-icon keyword map (the picker scores against this) | ✅ yes |
| `hook_overlays/*.png` | overlay icon glyphs | ⬜ you supply |
| `hooks/` · `ctas/` | ready hook / CTA clips (`*.mp4`/`*.mov`) | ⬜ you supply |
| `broll/` · `broll_brand/` · `broll_ingest/` | b-roll library + product demo clips | ⬜ you supply |
| `music/` | background music tracks (`config.yaml` → `edit_profile.music_file`) | ⬜ you supply |
| `sounds/` | SFX (riser, swoosh — referenced in `config.yaml`) | ⬜ you supply |
| `talking_head_sources/` | raw talking-head footage for the clean/ref-style pipelines | ⬜ you supply |

Use only media you have the rights to. Fonts referenced by `config.yaml` resolve
by family name against files in `fonts/`; the defaults map to the bundled
open-licensed fonts so subtitles render out of the box.
