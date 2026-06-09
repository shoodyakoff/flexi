---
name: video-montage
description: Complete video editing and montage pipeline for vertical reels (9:16, 1080x1920) with Claude Code. Covers subtitles, TTS voiceover, video assembly, text reels, 3-strip reels, audio mixing, color management, chat bubbles, and QA. Triggers on "montage", "edit video", "reels", "subtitles", "voiceover", "TTS", "text reel", "3-strip", "chat bubbles".
---

# Video Montage — Full Production Pipeline

Everything you need to produce vertical reels (9:16, 1080x1920, 30fps) with Claude Code — from raw footage to published content.

## Requirements

> **Note.** The project's production routes (see `CLAUDE.md`) run on the bundled
> `src/` engine and `requirements.txt` only — transcription there uses
> `faster-whisper` / `stable-ts`, **not** the `whisper` CLI. The tools marked
> *(optional, external)* below are only for the ad-hoc command-line recipes in
> this skill; install them separately when a recipe calls for one, and don't add
> them to `requirements.txt`.

- **ffmpeg** (with libass, drawtext filters)
- **Python 3** with Pillow (`pip install Pillow`)
- **ElevenLabs API key** (for TTS voiceover)
- **whisper** — *(optional, external)* OpenAI Whisper CLI: `pip install openai-whisper`. The pipelines themselves use `faster-whisper`/`stable-ts` instead.
- **yt-dlp** — *(optional, external)* for downloading reference reels: `pip install yt-dlp`

---

## 1. Subtitles

### 1.1 Extract audio

Always extract to 16kHz mono WAV before running Whisper:

```bash
ffmpeg -y -i video.MOV -ar 16000 -ac 1 -c:a pcm_s16le /tmp/subs/audio.wav
```

### 1.2 Whisper transcription

**Model selection:**

| Model | When to use | Speed |
|-------|-------------|-------|
| `small` | Quick QA checks, short clips | Fast |
| `medium` | Solo speech (one person talking to camera) | Medium |
| `large-v3` | Multi-speaker, AI assistant responses (Grok, ChatGPT TTS playback), quiet/distant audio | Slow (~10x vs medium) |

**Always use `--initial_prompt`** with domain-specific words to reduce hallucinations:

```bash
# Solo speech
whisper audio.wav --model medium --language ru --output_format srt --output_dir /tmp/subs/ \
  --initial_prompt "your domain words here, product names, slang terms"

# With AI assistant / multi-speaker
whisper audio.wav --model large-v3 --language ru --output_format srt --output_dir /tmp/subs/ \
  --initial_prompt "speaker names, AI names, product names, technical terms"
```

### 1.3 SRT → ASS conversion

Use this script to convert SRT to ASS with word chunking (3 words per subtitle segment for TikTok-style pacing):

```python
#!/usr/bin/env python3
"""gen_subs.py — Convert SRT to ASS with subtitle styles for vertical reels (1080x1920)

Usage:
    python3 gen_subs.py input.srt output.ass [--offset 6.67] [--font impact|helvetica]
"""

import argparse, re, os

ASS_HEADER_TEMPLATE = """[Script Info]
Title: Reel Subtitles
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style_line}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

STYLES = {
    "impact": "Style: Default,Impact,90,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,2,0,1,8,0,2,60,60,480,1",
    "helvetica": "Style: Default,Helvetica Neue,80,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,2,0,1,3,0,2,60,60,480,1",
}

def parse_srt_time(s):
    s = s.strip().replace(',', '.')
    h, m, rest = s.split(':')
    sec, ms = rest.split('.')
    return int(h)*3600 + int(m)*60 + int(sec) + int(ms)/1000

def fmt_ass_time(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    cs = round((s - int(s)) * 100)
    if cs >= 100: cs = 99
    return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"

def chunk_words(text, max_words=3):
    words = text.split()
    return [' '.join(words[i:i+max_words]) for i in range(0, len(words), max_words)]

def srt_to_ass(srt_path, ass_path, offset=0.0, max_words=3, font="impact"):
    with open(srt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    blocks = re.split(r'\n\n+', content.strip())
    entries = []
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3: continue
        m = re.match(r'(\S+)\s+-->\s+(\S+)', lines[1])
        if not m: continue
        t_start = parse_srt_time(m.group(1)) + offset
        t_end = parse_srt_time(m.group(2)) + offset
        text = ' '.join(lines[2:]).strip()
        chunks = chunk_words(text, max_words)
        if not chunks: continue
        duration = t_end - t_start
        chunk_dur = duration / len(chunks)
        for i, chunk in enumerate(chunks):
            cs = t_start + i * chunk_dur
            ce = cs + chunk_dur
            entries.append((fmt_ass_time(cs), fmt_ass_time(ce), chunk))
    style_line = STYLES.get(font, STYLES["impact"])
    header = ASS_HEADER_TEMPLATE.format(style_line=style_line)
    os.makedirs(os.path.dirname(ass_path) if os.path.dirname(ass_path) else '.', exist_ok=True)
    with open(ass_path, 'w', encoding='utf-8') as f:
        f.write(header)
        for s, e, t in entries:
            f.write(f"Dialogue: 0,{s},{e},Default,,0,0,0,,{t}\n")
    print(f"ASS: {len(entries)} chunks (offset={offset:.2f}s, font={font}) -> {ass_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("srt", help="Input SRT file")
    parser.add_argument("ass", help="Output ASS file")
    parser.add_argument("--offset", type=float, default=0.0)
    parser.add_argument("--max-words", type=int, default=3)
    parser.add_argument("--font", choices=["impact", "helvetica"], default="impact")
    args = parser.parse_args()
    srt_to_ass(args.srt, args.ass, offset=args.offset, max_words=args.max_words, font=args.font)
```

**Font styles:**

| Style | Font | Size | Outline | Best for |
|-------|------|------|---------|----------|
| `impact` | Impact Bold | 90pt | 8px black | Meme/TikTok style, voiceover reels |
| `helvetica` | Helvetica Neue Bold | 80pt | 3px black | Talking head, conversations, clean look |

### 1.4 Bake subtitles into video

```bash
ffmpeg -y -i source_video.MOV \
  -vf "format=yuv420p,ass=subs.ass" \
  -c:v libx264 -preset medium -crf 18 \
  -c:a aac -b:a 192k \
  -movflags +faststart \
  output_with_subs.mp4
```

**Important:** Always use `libx264 + format=yuv420p`. Never use `hevc_videotoolbox` — files may not open in iCloud/QuickTime.

---

## 2. TTS Voiceover (ElevenLabs)

### 2.1 Core rule: never monolithic

Never generate a single TTS call for speech longer than 15 seconds. Always split into segments — this gives you control over pacing and prevents phrase-mushing.

### 2.2 Segment your script

Tag each phrase with a pause type:

```
[seg] First sentence of your script. [pause:short]
[seg] Second sentence, same topic. [pause:medium]
[seg] New topic begins here. [pause:long]
[seg] The big conclusion or CTA.
```

### 2.3 Pause presets

| Pause | Duration | When to use |
|-------|----------|-------------|
| short | 0.16s | Between regular phrases within same topic |
| medium | 0.34s | Between topics or sections |
| long | 0.55s | Before CTA, key numbers, dramatic conclusion |

Generate silence files:

```bash
ffmpeg -y -f lavfi -i anullsrc=r=44100:cl=mono -t 0.16 -q:a 9 silence_short.mp3
ffmpeg -y -f lavfi -i anullsrc=r=44100:cl=mono -t 0.34 -q:a 9 silence_medium.mp3
ffmpeg -y -f lavfi -i anullsrc=r=44100:cl=mono -t 0.55 -q:a 9 silence_long.mp3
```

### 2.4 Generate each segment

```bash
curl -s -X POST "https://api.elevenlabs.io/v1/text-to-speech/YOUR_VOICE_ID" \
  -H "xi-api-key: $ELEVENLABS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Your segment text here.",
    "model_id": "eleven_multilingual_v2",
    "voice_settings": {
      "stability": 0.5,
      "similarity_boost": 0.75,
      "style": 0.0,
      "use_speaker_boost": true
    }
  }' --output segment_01.mp3
```

**Tuning tips:**
- Increase `style` to 0.2-0.4 for energetic/emotional delivery
- Increase `stability` to 0.6-0.7 for calm/measured narration
- Never exceed `style: 0.8` — distortion

### 2.5 Concatenate with pauses

Build a concat list:

```
file 'segment_01.mp3'
file 'silence_short.mp3'
file 'segment_02.mp3'
file 'silence_medium.mp3'
file 'segment_03.mp3'
```

Concat and adjust tempo:

```bash
ffmpeg -y -f concat -safe 0 -i concat.txt -c:a libmp3lame -q:a 2 vo_raw.mp3
ffmpeg -y -i vo_raw.mp3 -af "atempo=1.20" vo_final.mp3
```

**Tempo range:** 1.15-1.25x is natural. Never exceed 1.40x — causes distortion.

### 2.6 Anti-patterns

- Monolithic TTS for >15s speech — phrases will mush together
- Relying on punctuation alone for pacing — ElevenLabs ignores most pauses
- atempo > 1.40x — distorted robot voice
- Skipping silence files — natural speech has pauses

---

## 3. Video Assembly

### 3.1 Clip preparation

**CFR 30fps normalization (CRITICAL):**

Every clip must be constant frame rate before concatenation. VFR + CFR = freeze frames and duplicates.

```bash
ffmpeg -y -i clip.MOV \
  -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30" \
  -c:v libx264 -crf 18 -preset fast \
  -color_range 2 -colorspace bt709 -color_trc bt709 -color_primaries bt709 \
  -c:a aac -b:a 192k \
  clip_prepped.mp4
```

**iPhone vertical clips:** ffprobe may show 1920x1080 + rotation=-90. Always extract a frame to verify orientation before using.

```bash
ffmpeg -ss 1 -i clip.MOV -vframes 1 /tmp/check_orientation.jpg
```

### 3.2 Ken Burns effect

For static images or single-shot clips, add slow zoom to keep visuals interesting. Max 5-7 seconds per static shot.

```bash
ffmpeg -y -loop 1 -i photo.jpg -t 6 \
  -vf "scale=1120:1992,zoompan=z='min(zoom+0.0008,1.05)':d=180:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920,fps=30" \
  -c:v libx264 -crf 18 -pix_fmt yuv420p \
  ken_burns.mp4
```

### 3.3 Visual-narrative sync

When editing voiceover reels, match visuals to what the narrator is talking about:
- ~80% of clips should directly illustrate the current topic
- ~20% can be cutaway clips (gym, walking, typing) for visual variety between topics

**Workflow:**
1. Write a timeline: `0:00-0:15 topic A, 0:15-0:40 topic B, 0:40-1:00 CTA`
2. Assign clips to each slot based on topic
3. Alternate camera angles (handheld vs tripod) for variety

### 3.4 Concatenation

**Same codec (all prepped to libx264):**

```bash
# concat.txt:
file 'clip1_prepped.mp4'
file 'clip2_prepped.mp4'
file 'clip3_prepped.mp4'

ffmpeg -y -f concat -safe 0 -i concat.txt -c copy body.mp4
```

**Different codecs (filter_complex):**

```bash
ffmpeg -y -i clip1.mp4 -i clip2.mp4 -i clip3.mp4 \
  -filter_complex "[0:v][0:a][1:v][1:a][2:v][2:a]concat=n=3:v=1:a=1[v][a]" \
  -map "[v]" -map "[a]" \
  -c:v libx264 -crf 18 -c:a aac -b:a 192k \
  body.mp4
```

### 3.5 Intro/outro overlays

Layer PNG elements over video using overlay filter with timing:

```bash
ffmpeg -y -i bg_video.mp4 -i title.png -i logo.png \
  -filter_complex "
    [0:v][1:v]overlay=x=(W-w)/2:y=300:enable='between(t,0,5)'[tmp];
    [tmp][2:v]overlay=x=(W-w)/2:y=800:enable='between(t,1,5)'
  " \
  -c:v libx264 -crf 18 -c:a copy \
  intro.mp4
```

### 3.6 YAML-driven batch builds

Structure your episodes as YAML for repeatable builds:

```yaml
id: ep_001
vo: voiceover/ep001_clean.mp3
bgm: assets/bgm.mp3
bgm_vol: 0.08
clips:
  - file: clip_a.mp4
    ss: 0
    to: 8
  - file: clip_b.mp4
    ss: 5
    to: 15
  - file: clip_c.mp4
    ss: 0
    to: 10
subtitles: subs/ep001.srt
```

---

## 4. Text Reels

### 4.1 Format

B-roll video clip + styled text overlay + background music. No voiceover. Used for quotes, tips, stats, listicles.

### 4.2 Text overlay

Generate text as transparent PNG with Pillow, then overlay:

```python
from PIL import Image, ImageDraw, ImageFont

def create_text_overlay(text, output_path, font_size=50, max_chars_per_line=30):
    canvas = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype("/System/Library/Fonts/HelveticaNeue.ttc", font_size, index=1)

    # Word wrap
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = f"{current} {word}".strip()
        if len(test) <= max_chars_per_line:
            current = test
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)

    # Auto-reduce font size if too tall
    while font_size > 36:
        total_h = sum(draw.textbbox((0,0), l, font=font)[3] for l in lines) + 10 * (len(lines)-1)
        if total_h < 1920 * 0.7:
            break
        font_size -= 4
        font = ImageFont.truetype("/System/Library/Fonts/HelveticaNeue.ttc", font_size, index=1)

    # Draw centered with outline
    total_h = sum(draw.textbbox((0,0), l, font=font)[3] for l in lines) + 10 * (len(lines)-1)
    y = (1920 - total_h) // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        x = (1080 - w) // 2
        # Outline
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                if dx or dy:
                    draw.text((x+dx, y+dy), line, font=font, fill=(0,0,0,255))
        draw.text((x, y), line, font=font, fill=(255,255,255,255))
        y += bbox[3] + 10

    canvas.save(output_path)
```

### 4.3 Background dimming

Dim the b-roll clip so text is readable:

```bash
# dim = 0.27 means 27% black overlay (good default for text reels)
ffmpeg -y -i clip.mp4 -i text_overlay.png \
  -filter_complex "
    [0:v]drawbox=c=black@0.27:t=fill[dimmed];
    [dimmed][1:v]overlay=0:0
  " \
  -c:v libx264 -crf 18 -c:a copy \
  text_reel.mp4
```

### 4.4 Source audio from trending reels

Extract audio from a trending reel to reuse its music/vibe:

```bash
yt-dlp -f bestaudio -o "source_audio.%(ext)s" "https://www.instagram.com/reel/ABC123/"
# or extract from already downloaded video:
ffmpeg -y -i trending_reel.mp4 -vn -c:a copy source_audio.m4a
```

Mix with your clip:

```bash
ffmpeg -y -i text_reel_silent.mp4 -i source_audio.m4a \
  -filter_complex "[1:a]volume=0.15[bgm];[0:a][bgm]amix=inputs=2:duration=first[a]" \
  -map 0:v -map "[a]" \
  -c:v copy -c:a aac -b:a 192k \
  text_reel_final.mp4
```

---

## 5. Audio

### 5.1 BGM mixing

| Content type | BGM volume |
|-------------|-----------|
| Voiceover reel | 5-8% |
| Text reel (no voice) | 15-20% |
| Dramatic moment | 3-5% (duck under key phrase) |

```bash
ffmpeg -y -i body.mp4 -i bgm.mp3 \
  -filter_complex "
    [1:a]aloop=loop=-1:size=2e+09,atrim=duration=60,volume=0.08[bgm];
    [0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[a]
  " \
  -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 192k \
  final.mp4
```

### 5.2 SFX timing

Place a sound effect at a specific timestamp:

```bash
ffmpeg -y -i body.mp4 -i sfx.mp3 \
  -filter_complex "
    [1:a]volume=0.25,adelay=2000|2000[sfx];
    [0:a][sfx]amix=inputs=2:duration=first[a]
  " \
  -map 0:v -map "[a]" -c:v copy -c:a aac \
  body_with_sfx.mp4
```

`adelay=2000|2000` = 2 seconds delay (in milliseconds, both channels).

### 5.3 VO cleanup

**Find silences > 0.3s:**

```bash
ffmpeg -i vo.mp3 -af "silencedetect=noise=-30dB:d=0.3" -f null - 2>&1 | grep "silence_end"
```

**Find stumbles via word timestamps:**

```bash
whisper vo.mp3 --model small --language ru --word_timestamps True --output_format json --output_dir /tmp/
```

**Trim silence at start (iPhone recordings often have 0.5-1.5s silence):**

```bash
# Find where speech starts:
ffmpeg -i vo.mp3 -af "silencedetect=n=-40dB:d=0.1" -f null - 2>&1 | grep "silence_end"
# Trim (e.g., silence ends at 0.8s):
ffmpeg -y -i vo.mp3 -ss 0.8 -c:a copy vo_trimmed.mp3
```

---

## 6. Color and Format

### 6.1 iPhone HDR handling

iPhone records HEVC with HLG/bt2020 10-bit color. Most players/browsers can't display this correctly.

**Check source format:**

```bash
ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,pix_fmt,color_space,color_transfer,color_primaries \
  video.MOV
```

If you see `bt2020` / `arib-std-b67` / `yuv420p10le` — it's HDR, needs conversion.

### 6.2 SDR conversion

Always transcode to bt709 8-bit for delivery:

```bash
ffmpeg -y -i hdr_video.MOV \
  -vf "format=yuv420p" \
  -c:v libx264 -preset medium -crf 18 \
  -color_primaries bt709 -color_trc bt709 -colorspace bt709 \
  -c:a aac -b:a 192k -movflags +faststart \
  sdr_output.mp4
```

### 6.3 Color preservation

On every libx264 encode, tag color properly to prevent gradual desaturation:

```
-color_range 2 -colorspace bt709 -color_trc bt709 -color_primaries bt709
```

### 6.4 Codec selection

| Use case | Codec | CRF | Notes |
|----------|-------|-----|-------|
| Master delivery | libx264 | 18 | Always |
| Telegram/compressed | libx264 | 28-30 | Scale to 720p if needed for <50MB |
| Preview | libx264 | 30 | Quick check |
| Never | hevc_videotoolbox | — | Files may not open in iCloud/QuickTime |

Always add `-movflags +faststart` for web playback.

---

## 7. Chat Bubbles

iMessage/Telegram-style text bubbles overlaid on video. Great for "AI getting tasks" or "multitasking" format.

### 7.1 Generate bubble (Python/PIL)

```python
from PIL import Image, ImageDraw, ImageFont

def create_bubble(text, color=(52, 199, 89), width=400):
    font = ImageFont.truetype("/System/Library/Fonts/HelveticaNeue.ttc", 22, index=1)
    pad_x, pad_y, radius = 18, 10, 16
    alpha = 235

    # Measure text
    tmp = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(tmp)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    bw = tw + pad_x * 2
    bh = th + pad_y * 2
    tail_h = 12

    img = Image.new("RGBA", (bw + 20, bh + tail_h + 10), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Shadow
    shadow_color = (color[0]//2, color[1]//2, color[2]//2, 80)
    draw.rounded_rectangle([12, 4, bw+12, bh+4], radius, fill=shadow_color)

    # Bubble body
    draw.rounded_rectangle([10, 2, bw+10, bh+2], radius, fill=(*color, alpha))

    # Tail triangle
    cx = bw // 2 + 10
    draw.polygon([(cx-6, bh+2), (cx+6, bh+2), (cx, bh+tail_h+2)], fill=(*color, alpha))

    # Text
    draw.text((10 + pad_x, 2 + pad_y), text, font=font, fill=(255, 255, 255, 255))

    return img
```

### 7.2 Color presets

| Color | RGB | Use |
|-------|-----|-----|
| Green | (52, 199, 89) | Personal / iMessage style |
| Blue | (0, 122, 255) | Business / work tasks |

### 7.3 Overlay with timing

```bash
ffmpeg -y -i video.mp4 -i bubble1.png -i bubble2.png \
  -filter_complex "
    [0:v][1:v]overlay=x=(W-w)/2:y=300:enable='between(t,2,5)'[tmp];
    [tmp][2:v]overlay=x=(W-w)/2:y=500:enable='between(t,6,9)'
  " \
  -c:v libx264 -crf 18 -c:a copy \
  video_with_bubbles.mp4
```

### 7.4 Safe zones

- **Top 30%** — keep free (platform UI, captions)
- **Bottom 15%** — keep free (TikTok/Reels buttons, comments)
- **Safe Y range:** 220-650 for 720p equivalent
- Vary bubble positions between cuts (never repeat same Y on adjacent clips)

### 7.5 Gotchas

- PIL doesn't render emoji — use text only
- Check for letterbox bars with `cropdetect` — never place bubbles on black bars
- Use multiline for long text or reduce font size

---

## 8. QA and Delivery

### 8.1 Frame extraction

Always visually check your output before delivery:

```bash
# Extract frames at key timestamps
for t in 1 5 10 20 30; do
  ffmpeg -y -ss $t -i final.mp4 -vframes 1 -q:v 2 /tmp/check_t${t}s.jpg
done
```

Verify:
- Subtitles visible and correctly positioned?
- Correct font and outline?
- No artifacts, no double-baked text from prior renders?
- Overlays positioned correctly?

### 8.2 Whisper verification

Run Whisper on the final export to verify speech matches your script:

```bash
whisper final.mp4 --model small --language ru --output_format txt --output_dir /tmp/qa/
```

### 8.3 Ending truncation check

The `-shortest` flag can silently cut the last phrase. Always verify:

```bash
# Check duration
ffprobe -v error -show_entries format=duration -of csv=p=0 final.mp4

# Whisper-verify last 10 seconds
ffmpeg -y -sseof -10 -i final.mp4 -vn -c:a pcm_s16le /tmp/ending.wav 2>/dev/null
whisper /tmp/ending.wav --model small --output_format txt --output_dir /tmp/qa/
```

### 8.4 Export formats

**Master:**
```bash
# 1080x1920, high quality
ffmpeg -y -i assembled.mp4 \
  -c:v libx264 -crf 18 -preset medium \
  -c:a aac -b:a 192k \
  -movflags +faststart \
  master.mp4
```

**Compressed (Telegram / <50MB):**
```bash
ffmpeg -y -i master.mp4 \
  -vf "scale=720:1280" \
  -c:v libx264 -crf 28 \
  -c:a aac -b:a 96k \
  -movflags +faststart \
  compressed.mp4
```

---

## 9. Hard Rules (Learned the Hard Way)

1. **CFR before concat** — ALL segments must be constant 30fps before any concatenation. VFR + CFR = freeze frames, duplicated frames, audio drift.

2. **Never overlay on baked text** — if a video already has burned-in subtitles, never add more on top. Always rebuild from source layers. Double-baked subs produce ghost text.

3. **Max 3 versions rule** — if after v3 the result still doesn't work, stop and diagnose systematically. Don't keep patching symptoms — find the root cause.

4. **Diagnose before rebuild** — run ffprobe, extract frames, check logs BEFORE starting a new build attempt.

5. **Subtitle text must be manually verified** — Whisper (even large-v3) makes plausible-looking errors. Never trust raw output.

6. **VO silence at start** — iPhone recordings always have 0.5-1.5s silence at the beginning. Always detect and trim.

7. **Match visuals to narration** — when the narrator says "email", show email. When they say "product", show the product. 80% topic-matched clips, 20% cutaways.

8. **Check orientation before using clips** — ffprobe metadata can lie about rotation. Always extract a frame and look at it.

9. **Never use `hevc_videotoolbox` for final delivery** — QuickTime and iCloud may refuse to open the file. Use `libx264 + yuv420p` always.

10. **Always `movflags +faststart`** — without this, the video won't stream properly on web/mobile.

---

## 10. 3-Strip Reels (three horizontal clips stacked)

A 9:16 frame split into **three horizontal bands** (~632px each, thin black
gaps). Shoot horizontal clips on purpose so three stack cleanly. There is a
ready config-driven engine — prefer it over hand-rolling ffmpeg:

```bash
cp pipelines/three_strip/episodes/example.conf pipelines/three_strip/episodes/myday.conf
# author MID/TOP/BOT (index-aligned columns) in the conf, then:
zsh pipelines/three_strip/build_3strip.zsh pipelines/three_strip/episodes/myday.conf
```

Key technique (encoded in the engine; see `pipelines/three_strip/RULES.md`):

- **Asynchronous cascade** — middle band leads at t=0, top enters +0.5s, bottom
  +1.0s, then each band cuts on its own rhythm. Do NOT cut all three together.
- **Bands** — each `scale=1080:632 + center-crop`, main action centered.
- **HDR→SDR** — tonemap 4K HLG with `zscale+tonemap` (plain `format=yuv420p`
  washes out). Source audio off; quiet background music; bed bookend.
- **Variety rule** — never three lookalike clips in one trio; max one screen per
  column; sprinkle face/reaction beats.
- **Orientation** — iPhone tags lie; verify by eye and list flips in `NEED180`.
- Build auto-runs `three_strip/qa.py` (format, no-3-same via phash≥10, dead
  bands, face gaps) → `qa_report.txt` + annotated `qa_sheet.png`. Ship at PASS.
