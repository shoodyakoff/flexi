# Video creation modes

This is the working contract for choosing the right reel workflow.

## Content modes

A reel falls into one of a few editorial shapes. These are conceptual — the
inputs are plain `VideoScript` JSON in `scripts/`:

1. **`generated_video`** — AI reel "под ключ": hook + body voiceover + CTA, with
   `hook_asset_id`, `cta_asset_id`, and an optional product insert.
2. **`talking_head`** — the creator speaks on camera (one or more raw takes) and
   the footage is cleaned/edited into a vertical reel.
3. **`explain_with_images`** — self-shot explainer where the creator speaks and
   uses a laptop, screen, screenshots, or simple images as visual anchors. Do
   not apply TTS pause markup for this one.

## Production routes

When asked to create a reel, choose one route before writing or building.

### Standard mode

`standard mode` is the default production contract for commands like:

> собери 10 роликов в стандартном режиме с 11 по 20

Use it when the user has already prepared the `VideoScript` JSON in `scripts/` and dropped the needed raw media into the asset folders.

Standard mode means:

- Content mode: `generated_video`.
- Production route: `library_auto`.
- TTS preset: `v2`.
- ElevenLabs model: `eleven_multilingual_v2`.
- ElevenLabs speed: `1.15`.
- TTS markup dialect: `v2`.
- Language: `ru`.
- Pronunciation dictionary: enabled from `config.yaml`.
- Whisper alignment/transcription: `large-v3`.
- Subtitle style: `editorial_pop`.
- Rhythm profile: `provocative_soft`.
- Look profile: `punchy`, with `punchy_bright` for dark hooks.
- B-roll strategy: `auto`.
- Music: configured profile music, currently `assets/music/provocative.mp3`.
- Product insert: add your product demo clip from `assets/broll_brand/` when the script calls for a product/demo moment.

Before rendering in standard mode, the agent must complete the prep work:

1. Read `VOICEOVER_STYLE.md` and make sure `voiceover_text` uses `{{pause:...}}` and `{{slow}}...{{/slow}}`.
2. Prepare or read the requested `VideoScript` JSON files in `scripts/`.
3. Inspect new media files in `assets/hooks`, `assets/ctas`, `assets/broll`, and relevant ingest folders.
4. Match media files to reel numbers by filename first, then by creation time and visible/audio content if needed.
5. Rename matched files into the project asset convention instead of building from ad-hoc filenames.
6. Update asset metadata for hooks, CTAs, and b-roll.
7. For b-roll, fill the required annotation fields: `shot_scale`, `subject_kind`, `energy`, `scene_group`.
8. Pick or verify `hook_asset_id` and `cta_asset_id` in each script.
9. Write each `VideoScript` to `scripts/{slug}.json`.
10. Validate each exported script before rendering.
11. Build the videos with the default config, without asking again for voice speed, subtitle style, TTS model, music, or b-roll strategy.
12. After rendering, report the final paths and any failed builds.

Ask the user only when matching is ambiguous or a required asset is missing. For example, ask if two CTA files both look like reel 14, or if reel 17 has no matching CTA/hook footage.

### 1. Library auto reel

Use when the user wants a standard product / advice reel assembled from existing assets.

- Script format: `VideoScript`.
- Hook and CTA: existing assets from `assets/hooks` and `assets/ctas`.
- Body: ElevenLabs voiceover from `voiceover_text`.
- B-roll: `broll_strategy: "auto"` or `"by_tags"`.
- Product insert: use `product_insert` when the product/demo appears.
- Output: `scripts/{slug}.json`, then `python -m src.cli build scripts/{slug}.json`.

Recommended default.

### 2. Library manual reel

Use when the user wants the same renderer, but specific lower-row / body clips in a precise order.

- Script format: `VideoScript`.
- Set `broll_strategy: "manual"`.
- Fill `broll_ids_override` with the chosen clip ids.
- Good for: testing pacing, forcing a visual sequence, repeating a proven composition.
- Tradeoff: less automatic variety and more responsibility for clip order.

### 3. Custom graphics reel

Use when the reel needs generated visuals, editorial graphics, screenshots, split-screen, cartoon panels, or a designed lower visual row that the b-roll library cannot express.

- Preferred implementation: HyperFrames/HTML video composition for graphic-heavy pieces.
- Good for: “make the lower row with generated video/graphics”, diagrams, UI/product walkthroughs, memes, visual metaphors.
- Output: a rendered MP4 from the custom composition, optionally combined with the standard hook/body/CTA renderer later.
- Tradeoff: more design freedom, but more bespoke build/QA per episode.

### 4. Hybrid reel

Use when the top-level structure is standard, but the body needs one custom graphic segment.

- Standard hook + CTA assets.
- Standard TTS/subtitles.
- Body is either:
  - standard b-roll with one generated graphic insert, or
  - a custom rendered body segment fed into final assembly.
- This is the likely target for the “нижний ряд с видеороликами/графикой” workflow.

### 5. Talking head dynamic clean

Use when the user provides one long talking-head video, or several talking-head
takes, and wants a clean dynamic edit before adding subtitles, music, labels, or
other reel effects.

- Content mode: typically a `talking_head` source — any raw talking-head
  footage that should become a vertical reel.
- Production route: `talking_head_dynamic_clean`.
- Source: one or more raw video files.
- Output: `1080x1920` vertical video with cleaned speech, dynamic framing, and
  light transitions between cuts.
- Do not add subtitles, captions, headings, music, brand anchors, stickers, look
  filters, or decorative overlays by default.
- If the user asks to assemble a talking-head reel and does not explicitly say
  whether subtitles are needed, ask one short question before rendering:
  "с субтитрами или только чистая нарезка?"
- If subtitles are requested, use the standard-mode subtitle defaults unless the
  user asks otherwise: `edit_profile.subtitle_style` from `config.yaml`, currently
  `editorial_pop`. Keep `final_clean.mp4` as the clean cut and also write
  `final_subtitled.mp4`, `subtitles.ass`, and the subtitle timing artifacts.
- Remove dead air, long pauses, obvious restarts, failed takes, and duplicate
  attempts when the better take is present.
- Keep short natural pauses when they carry rhythm or meaning; the edit should
  feel human, not over-compressed.
- In `smart` retake mode, transcribe the source and remove failed repeated
  attempts by text similarity, not by silence alone. Write automatic and
  review-only decisions to `retake_decisions.json`.
- Add a transition between visible cuts. The default transition is a very short
  cinematic micro-push, about 0.12-0.20 seconds. Use stronger swipes only
  between major semantic blocks.
- Change framing every 2-4 seconds, paced to speech and pauses. Prefer plan
  changes at phrase endings, before new arguments, and around emphasis points.

Frame types:

- `medium`: baseline talking-head composition. For horizontal sources, preserve
  the full-width talking-head scale on a clean neutral canvas; do not use a
  blurred duplicate of the source as background. The speaker should feel
  comfortable and not too close.
- `close`: tighter crop for punchlines, emotionally important phrases, key
  conclusions, and short emphasis moments. For horizontal sources, use a
  tighter full-width framed `medium_close` instead of a vertical face crop.
- `medium_with_broll`: a dynamic frame change where the talking-head composition
  keeps exactly the same foreground scale as `medium`, then the full talking-head
  frame moves upward and a relevant b-roll lane appears below. The head must not
  become larger; the cinematic effect comes from changing composition, not
  zooming in.

Agent workflow:

1. Ingest the raw video or videos and create `output/{slug}`.
   - If raw talking-head videos were dropped into the project root, move them
     and matching `{stem}.transcript.json` / `{stem}.transcript.json.hash`
     sidecars into `assets/talking_head_sources/{slug}/` before processing.
     The renderer applies this rule automatically for root-level inputs.
2. Detect speech, pauses, and candidate cut points from audio.
3. Remove only clear long silences and obvious unusable gaps automatically.
   Keep enough audio padding around speech so quiet syllables and phrase endings
   are not clipped.
4. Run retake planning unless disabled: group repeated attempts by transcript
   similarity, apply confident safe/smart choices, and leave uncertain groups in
   `retake_decisions.json` as `needs_review`.
5. Split the remaining speech into 2-4 second visual beats.
6. Assign frame types so adjacent beats feel like intentional shot changes:
   `medium`, `close`, then `medium_with_broll` when suitable b-roll exists.
7. For `medium_with_broll`, use only relevant clean b-roll. If no matching b-roll
   exists, choose `medium` or `close` instead of inserting random footage.
8. Render `final_clean.mp4`, `edit_decisions.json`, `retake_decisions.json`,
   and `render_metadata.json`.
9. If subtitles were requested, build a final timeline transcript from the kept
   chunks, generate standard ASS subtitles, and burn them into
   `final_subtitled.mp4`.
10. QA the result for audio sync, subtitle sync when enabled, accidental black frames, bad face crop, harsh
   cuts, and unwanted text/effects.

## Recommended defaults

- For product / advice reels: `standard mode`.
- For controlled tests with existing clips: `generated_video` + `library_manual`.
- For agent-process / visual explanation reels: `generated_video` + `hybrid`.
- For fully self-shot “объяснять с картинками” reels: `explain_with_images` + `hybrid`.
- For raw talking-head footage: `talking_head` + `talking_head_dynamic_clean`.
- For a day of horizontal clips: the **3-strip** route (`pipelines/three_strip/`).
