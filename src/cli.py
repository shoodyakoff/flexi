from __future__ import annotations
import json
import os
import re
import subprocess
import time
from datetime import date
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .assets import (
    ASSETS_DIR,
    get_asset_path,
    list_assets,
    load_asset_meta,
    resolve_project_path,
    save_asset_meta,
    validate_assets,
)
from .asset_transcribe import transcribe_asset
from .assembly import (
    _cta_duration_limit_sec,
    _cta_lead_trim_sec,
    build_broll_section,
    concat_final,
    inspect_first_frames,
    probe_clip_luma,
)
from .broll_library import scan_broll_library
from .broll_preview import render_broll_preview
from .advanced_runtime import build_block_plan
from .diagnostics import write_render_diagnostics
from .hook_montage import hook_effect_end_sec, render_hook_montage
from .output_paths import latest_version_dir, seed_reusable, versioned_dir
from .rhythm_planner import plan_broll
from .schemas import (
    AssetEntry,
    Config,
    HookMontageDiagnostics,
    RenderPlan,
    SubtitleStyle,
    Transcript,
    VideoScript,
)
from .subtitles import subtitle_diagnostics, transcript_to_ass
from .transcribe import align_to_text
from .tts_text import VoiceoverTextPlan, prepare_voiceover_text, split_product_breaks
from .tts import synthesize

app = typer.Typer(name="factory", add_completion=False)
console = Console()

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
_TTS_PRESETS = {
    "v2": {
        "model_id": "eleven_multilingual_v2",
        "markup_dialect": "v2",
    },
    "v3": {
        "model_id": "eleven_v3",
        "markup_dialect": "v3",
    },
}


def _load_config() -> Config:
    raw = yaml.safe_load(CONFIG_PATH.read_text())
    return Config(**raw)


def _load_script(script_path: Path) -> VideoScript:
    return VideoScript(**json.loads(script_path.read_text()))


def _normalize_tts_preset(preset: Optional[str]) -> Optional[str]:
    if preset is None:
        return None
    normalized = preset.strip().lower()
    if not normalized:
        return None
    if normalized not in _TTS_PRESETS:
        raise typer.BadParameter("tts preset must be one of: v2, v3")
    return normalized


def _with_tts_preset(cfg: Config, preset: Optional[str]) -> Config:
    normalized = _normalize_tts_preset(preset)
    if normalized is None:
        return cfg
    return cfg.model_copy(
        update={
            "tts": cfg.tts.model_copy(update=_TTS_PRESETS[normalized]),
        }
    )


def _infer_tts_preset(cfg: Config) -> str:
    for preset, values in _TTS_PRESETS.items():
        if (
            cfg.tts.model_id == values["model_id"]
            and cfg.tts.markup_dialect == values["markup_dialect"]
        ):
            return preset
    return "custom"


def _resolve_build_config(
    cfg: Config,
    vs: VideoScript,
    *,
    tts_preset: Optional[str] = None,
) -> tuple[Config, Optional[str]]:
    preset = _normalize_tts_preset(tts_preset) or vs.tts_preset
    return _with_tts_preset(cfg, preset), preset


_REEL_NUMBER_SLUG_RE = re.compile(r"(?:^|-)reel-(\d{1,3})(?:-|$)", re.IGNORECASE)
_NUMBERED_REEL_SLUG_RE = re.compile(r"(?:^|-)ролик-(\d{1,3})(?:-|$)", re.IGNORECASE)


def _numbered_reel_output_slug(slug: str) -> Optional[str]:
    match = _REEL_NUMBER_SLUG_RE.search(slug) or _NUMBERED_REEL_SLUG_RE.search(slug)
    if not match:
        return None
    return f"reel-{int(match.group(1)):03d}"


def _output_slug(slug: str, output_suffix: Optional[str]) -> str:
    numbered_reel_slug = _numbered_reel_output_slug(slug)
    if numbered_reel_slug:
        return numbered_reel_slug
    if output_suffix is None:
        return slug
    suffix = _slugify(output_suffix)
    return f"{slug}-{suffix}" if suffix else slug


def _subtitle_style_id(vs: VideoScript, cfg: Config) -> str:
    if vs.subtitle_style:
        return vs.subtitle_style
    return cfg.edit_profile.subtitle_style


def _subtitle_style(vs: VideoScript, cfg: Config) -> SubtitleStyle:
    style = cfg.subtitle_styles[_subtitle_style_id(vs, cfg)]
    if not vs.subtitle_accent_words and not vs.subtitle_accent_phrases:
        return style
    return style.model_copy(
        update={
            "accent_keywords": list(dict.fromkeys([*style.accent_keywords, *vs.subtitle_accent_words])),
            "accent_phrases": list(dict.fromkeys([*style.accent_phrases, *vs.subtitle_accent_phrases])),
        }
    )


def _with_subtitle_style(vs: VideoScript, cfg: Config, style_id: Optional[str]) -> VideoScript:
    if style_id is None:
        return vs
    normalized = style_id.strip()
    if not normalized:
        return vs
    if normalized not in cfg.subtitle_styles:
        available = ", ".join(sorted(cfg.subtitle_styles))
        raise typer.BadParameter(f"subtitle style must be one of: {available}")
    return vs.model_copy(update={"subtitle_style": normalized})


def _pick_subtitle_style(cfg: Config) -> str:
    style_ids = sorted(cfg.subtitle_styles)
    default_style = cfg.edit_profile.subtitle_style

    console.print("\n[bold]6. Subtitle style[/bold]")
    for index, style_id in enumerate(style_ids, start=1):
        marker = " default" if style_id == default_style else ""
        console.print(f"  [cyan]{index}[/cyan] {style_id}{marker}")
    default_index = style_ids.index(default_style) + 1 if default_style in style_ids else 1
    choice = typer.prompt("  Choice", default=str(default_index)).strip()
    if choice.isdigit():
        index = int(choice)
        if 1 <= index <= len(style_ids):
            return style_ids[index - 1]
    if choice in cfg.subtitle_styles:
        return choice
    console.print(f"  [yellow]Unknown style '{choice}', using {default_style}[/yellow]")
    return default_style



def _look_profile_id(vs: VideoScript, cfg: Config) -> str:
    if vs.look_profile:
        return vs.look_profile
    return cfg.edit_profile.look_profile


def _auto_look_profile(vs: VideoScript, cfg: Config, hook_path: Path) -> str:
    """Select look profile: explicit script override > auto dark-mode > edit_profile default."""
    if vs.look_profile:
        return vs.look_profile
    base = cfg.edit_profile.look_profile
    dark_profile = cfg.edit_profile.dark_look_profile
    if dark_profile is None or dark_profile not in cfg.look_profiles:
        return base
    luma = probe_clip_luma(hook_path)
    threshold = cfg.edit_profile.dark_luma_threshold
    if luma < threshold:
        console.log(
            f"[blue]→[/blue] Hook luma {luma:.1f} < {threshold:.0f} → auto dark mode: "
            f"[bold]{dark_profile}[/bold] (tip: lock AE/AF before recording to avoid exposure ramp)"
        )
        return dark_profile
    console.log(f"[blue]→[/blue] Hook luma {luma:.1f} → look profile: [bold]{base}[/bold]")
    return base


def _hook_subtitle_style_id(cfg: Config, fallback_style_id: str) -> str:
    fallback_style = cfg.subtitle_styles[fallback_style_id]
    if fallback_style.caption_mode == "editorial":
        return fallback_style_id
    if fallback_style_id != cfg.edit_profile.subtitle_style:
        return fallback_style_id
    return cfg.hook_montage.typography.style or fallback_style_id


def _with_hook_asset_accents(cfg: Config, hook_entry: AssetEntry) -> Config:
    if not hook_entry.accent_keywords:
        return cfg
    typography = cfg.hook_montage.typography
    merged_keywords = list(dict.fromkeys([
        *typography.accent_keywords,
        *hook_entry.accent_keywords,
    ]))
    if merged_keywords == typography.accent_keywords:
        return cfg
    return cfg.model_copy(
        update={
            "hook_montage": cfg.hook_montage.model_copy(
                update={
                    "typography": typography.model_copy(
                        update={"accent_keywords": merged_keywords}
                    )
                }
            )
        }
    )


def _resolve_music_path(cfg: Config) -> Path | None:
    if not cfg.edit_profile.music_file:
        return None

    music_path = resolve_project_path(cfg.edit_profile.music_file)
    if not music_path.exists():
        console.log(
            f"[yellow]![/yellow] Music file for edit profile is missing: {music_path}"
        )
        return None
    return music_path


def _resolve_product_insert(vs: VideoScript) -> Path | None:
    if vs.product_insert is None:
        return None
    return resolve_project_path(vs.product_insert.file)


def _pronunciation_rules_from_yaml(path: Path) -> list[dict[str, object]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules: list[dict[str, object]] = []
    for section in ("terms", "phrases"):
        entries = raw.get(section) or {}
        if not isinstance(entries, dict):
            raise typer.BadParameter(f"{path}: '{section}' must be a mapping")
        for source, alias in entries.items():
            source_text = str(source).strip()
            alias_text = str(alias).strip()
            if not source_text or not alias_text:
                continue
            rules.append(
                {
                    "string_to_replace": source_text,
                    "type": "alias",
                    "alias": alias_text,
                    "case_sensitive": False,
                    "word_boundaries": True,
                }
            )
    return rules


def _append_hook_cta_duration_warnings(
    render_plan: RenderPlan,
    hook_path: Path,
    cta_path: Path,
) -> None:
    for label, path in (("hook", hook_path), ("cta", cta_path)):
        duration = _ffprobe_duration(path)
        if duration > 10.0:
            render_plan.warnings.append(
                f"{label} asset is {duration:.1f}s; review pacing manually"
            )


def _enforce_render_guardrails(
    cfg: Config,
    *,
    hook_montage: HookMontageDiagnostics,
    cta_duration_sec: float,
) -> None:
    if (
        cfg.render_guardrails.fail_on_long_hook
        and hook_montage.enabled
        and hook_montage.final_duration_sec
        and hook_montage.final_duration_sec > cfg.hook_montage.target_max_sec
    ):
        raise RuntimeError(
            f"Render guardrail failed: hook montage is {hook_montage.final_duration_sec:.2f}s, "
            f"target max is {cfg.hook_montage.target_max_sec:.2f}s"
        )

    if cfg.render_guardrails.fail_on_missing_hook_accent and hook_montage.enabled:
        has_accent_effect = bool(
            hook_montage.sfx_events
            or hook_montage.overlays
            or any(
                event.startswith(("keyword_punch:", "riser_zoom_out_in:"))
                for event in hook_montage.motion_events
            )
        )
        if not has_accent_effect:
            raise RuntimeError(
                "Render guardrail failed: hook has no accent effect "
                "(no overlay, sfx, or motion event)"
            )

    # The CTA target is a pacing target, not a hard trim point. The normalizer
    # may exceed it to preserve the last spoken word; clipping speech is worse
    # than shipping a slightly longer CTA.


def _normalized_cta_duration_sec(
    cta_path: Path,
    cta_transcript: Transcript,
    cfg: Config,
) -> float:
    media_duration_sec = _ffprobe_duration(cta_path)
    lead_trim_sec = _cta_lead_trim_sec(cta_transcript, media_duration_sec)
    return _cta_duration_limit_sec(
        cta_transcript,
        lead_trim_sec=lead_trim_sec,
        media_duration_sec=media_duration_sec,
        cfg=cfg,
    )


def _music_start_sec(cfg: Config, hook_montage: HookMontageDiagnostics) -> float:
    if cfg.audio.music_start_mode == "full_reel":
        return 0.0
    music_start_sec = hook_effect_end_sec(hook_montage)
    if hook_montage.enabled and cfg.hook_montage.transition.enabled:
        music_start_sec = max(
            music_start_sec,
            (hook_montage.final_duration_sec or 0.0)
            + cfg.hook_montage.transition.duration_sec,
        )
    return music_start_sec


@app.command()
def build(
    script: Path = typer.Argument(..., help="Path to script JSON"),
    tts_preset: Optional[str] = typer.Option(
        None,
        "--tts-preset",
        help="Voice model preset for this run: v2 or v3.",
    ),
    subtitle_style: Optional[str] = typer.Option(
        None,
        "--subtitle-style",
        help="Subtitle style preset for this run, overriding the script JSON.",
    ),
    output_suffix: Optional[str] = typer.Option(
        None,
        "--output-suffix",
        help="Append a suffix to the output folder, useful for A/B runs.",
    ),
    version: Optional[str] = typer.Option(
        None,
        "--version",
        help="Render into a specific version subfolder (e.g. v2), overwriting it. "
        "Default: create the next vN so previous renders are kept.",
    ),
):
    """Run the full pipeline: TTS → transcribe → subtitles → b-roll → assemble."""
    vs = _load_script(script)
    cfg, active_preset = _resolve_build_config(_load_config(), vs, tts_preset=tts_preset)
    vs = _with_subtitle_style(vs, cfg, subtitle_style)
    _run_build(vs, cfg, output_suffix=output_suffix or active_preset or subtitle_style, version=version)


@app.command()
def preview(
    script: Path = typer.Argument(..., help="Path to script JSON"),
    tts_preset: Optional[str] = typer.Option(
        None,
        "--tts-preset",
        help="Voice model preset for this run: v2 or v3.",
    ),
    subtitle_style: Optional[str] = typer.Option(
        None,
        "--subtitle-style",
        help="Subtitle style preset for this run, overriding the script JSON.",
    ),
    output_suffix: Optional[str] = typer.Option(
        None,
        "--output-suffix",
        help="Append a suffix to the output folder, useful for A/B runs.",
    ),
    version: Optional[str] = typer.Option(
        None,
        "--version",
        help="Render into a specific version subfolder (e.g. v2), overwriting it. "
        "Default: create the next vN so previous renders are kept.",
    ),
):
    """TTS + subtitles only — no video assembly. Fast iteration on text."""
    vs = _load_script(script)
    cfg, active_preset = _resolve_build_config(_load_config(), vs, tts_preset=tts_preset)
    vs = _with_subtitle_style(vs, cfg, subtitle_style)

    preview_base = Path(cfg.output_dir) / _output_slug(vs.slug, output_suffix or active_preset or subtitle_style)
    previous_dir = latest_version_dir(preview_base)
    out_dir = versioned_dir(preview_base, version=version)
    seed_reusable(previous_dir, out_dir, ["voiceover.mp3", "*.hash", "transcript.json"])
    vo_path, transcript, _, _, _ = _prepare_voiceover_assets(vs, cfg, out_dir)
    style = _subtitle_style(vs, cfg)
    ass_path = out_dir / "subtitles.ass"
    transcript_to_ass(
        transcript,
        style,
        ass_path,
        safe_box=cfg.subtitle_safe_box,
        cfg=cfg,
        caption_plan_path=out_dir / "caption_plan.json" if style.caption_mode == "editorial" else None,
        section="body",
    )

    console.print(f"\n[bold]Preview ready.[/bold]")
    console.print(f"  Audio:     {vo_path}")
    console.print(f"  Subtitles: {ass_path}")
    console.print(f"\nCheck in VLC: [dim]vlc {vo_path} --sub-file {ass_path}[/dim]")


@app.command(name="sync-pronunciation")
def sync_pronunciation(
    dictionary_id: Optional[str] = typer.Option(
        None,
        "--dictionary-id",
        help="Existing ElevenLabs pronunciation dictionary ID to replace rules in.",
    ),
    name: str = typer.Option(
        "flexi-ru",
        "--name",
        help="Name for a new ElevenLabs pronunciation dictionary.",
    ),
    source: Path = typer.Option(
        ASSETS_DIR / "pronunciation.yaml",
        "--source",
        help="Local pronunciation YAML with terms/phrases aliases.",
    ),
):
    """Create or update an official ElevenLabs pronunciation dictionary."""
    if not source.exists():
        raise typer.BadParameter(f"pronunciation source not found: {source}")
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise typer.BadParameter("ELEVENLABS_API_KEY not set")

    rules = _pronunciation_rules_from_yaml(source)
    if not rules:
        raise typer.BadParameter(f"no pronunciation rules found in {source}")

    from elevenlabs import ElevenLabs

    client = ElevenLabs(api_key=api_key)
    if dictionary_id:
        response = client.pronunciation_dictionaries.rules.set(
            dictionary_id,
            rules=rules,
        )
        resolved_id = response.id
        version_id = response.version_id
    else:
        response = client.pronunciation_dictionaries.create_from_rules(
            rules=rules,
            name=name,
            description="Russian aliases synced from assets/pronunciation.yaml",
        )
        resolved_id = response.id
        version_id = response.version_id

    console.print("[bold green]✓ Pronunciation dictionary synced[/bold green]")
    console.print(f"  rules:      {len(rules)}")
    console.print(f"  dictionary: {resolved_id}")
    console.print(f"  version:    {version_id}")
    console.print("\nAdd this to config.yaml under tts.pronunciation_dictionary_locators:")
    console.print(
        f"  - pronunciation_dictionary_id: \"{resolved_id}\"\n"
        f"    version_id: \"{version_id}\""
    )


@app.command(name="annotate")
def annotate_script(
    script: Path = typer.Argument(..., help="Path to script JSON"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write to this path instead of editing in place."),
    in_place: bool = typer.Option(True, "--in-place/--no-in-place", help="Update the script file directly."),
):
    """Add deterministic voiceover pacing markers to a script JSON."""
    vs = _load_script(script)
    annotated_text = _annotate_voiceover_text(vs.voiceover_text)
    updated = vs.model_copy(update={"voiceover_text": annotated_text})
    out_path = script if in_place and output is None else output
    if out_path is None:
        out_path = script.with_name(f"{script.stem}.annotated{script.suffix}")
    out_path.write_text(
        updated.model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8",
    )
    console.print(f"[green]✓[/green] Annotated voiceover saved: {out_path}")


@app.command(name="list-assets")
def list_assets_cmd(
    asset_type: str = typer.Argument(..., help="hooks | broll | ctas"),
    tags: Optional[str] = typer.Option(None, "--tags", help="Comma-separated tag filter"),
):
    """Show available assets with their tags and duration."""
    filter_tags = [t.strip() for t in tags.split(",")] if tags else None
    meta = list_assets(asset_type, filter_tags=filter_tags)  # type: ignore[arg-type]

    table = Table(title=f"Assets: {asset_type}")
    table.add_column("ID", style="cyan")
    table.add_column("File")
    table.add_column("Duration", justify="right")
    table.add_column("Tags")
    table.add_column("Description")
    if asset_type == "broll":
        table.add_column("Shot")
        table.add_column("Subject")
        table.add_column("Energy", justify="right")
        table.add_column("Scene Group")
        table.add_column("Needs Annotation")

    for asset_id, entry in sorted(meta.items()):
        row = [
            asset_id,
            entry.file,
            f"{entry.duration:.1f}s",
            ", ".join(entry.tags),
            entry.description,
        ]
        if asset_type == "broll":
            row.extend([
                entry.shot_scale or "-",
                entry.subject_kind or "-",
                str(entry.energy or "-"),
                entry.scene_group or "-",
                "yes" if entry.needs_annotation else "no",
            ])
        table.add_row(*row)

    console.print(table)


@app.command()
def validate(script: Path = typer.Argument(..., help="Path to script JSON")):
    """Validate script JSON and referenced assets without running the pipeline."""
    cfg = _load_config()
    vs = _load_script(script)

    style_id = _subtitle_style_id(vs, cfg)
    if style_id not in cfg.subtitle_styles:
        raise typer.BadParameter(f"subtitle_style '{style_id}' not in config.yaml")
    hook_style_id = _hook_subtitle_style_id(cfg, style_id)
    if hook_style_id not in cfg.subtitle_styles:
        raise typer.BadParameter(f"hook subtitle_style '{hook_style_id}' not in config.yaml")
    if _look_profile_id(vs, cfg) not in cfg.look_profiles:
        raise typer.BadParameter(f"look_profile '{_look_profile_id(vs, cfg)}' not in config.yaml")
    rhythm_profile = cfg.edit_profile.rhythm_profile
    if rhythm_profile not in cfg.rhythm_profiles:
        raise typer.BadParameter(f"rhythm_profile '{rhythm_profile}' not in config.yaml")

    try:
        validate_assets(vs.slug, vs.hook_id, vs.cta_id)
    except (ValueError, FileNotFoundError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    if vs.broll_strategy == "manual" and not vs.broll_ids_override:
        raise typer.BadParameter("broll_ids_override required for strategy 'manual'")

    product_insert_path = _resolve_product_insert(vs)
    if product_insert_path is not None and not product_insert_path.exists():
        raise typer.BadParameter(f"product_insert file not found: {product_insert_path}")
    try:
        overlay_library = load_asset_meta("hook_overlays")
    except FileNotFoundError:
        overlay_library = {}
    for asset_id, entry in overlay_library.items():
        overlay_path = resolve_project_path(f"assets/hook_overlays/{entry.file}")
        if not overlay_path.exists():
            raise typer.BadParameter(
                f"hook overlay '{asset_id}' file missing on disk: {overlay_path}"
            )
    if vs.hook_overlay_id and vs.hook_overlay_id not in overlay_library:
        raise typer.BadParameter(
            f"hook_overlay_id '{vs.hook_overlay_id}' not in assets/hook_overlays/_meta.json"
        )

    console.print(f"[bold green]✓ Script valid:[/bold green] {script}")


@app.command()
def shnurok(
    folder: Path = typer.Argument(..., help="Folder with dropped sources for one reel"),
    style: str = typer.Option("both", help="classic | bold | both"),
):
    """Assemble a SHNUROK/NUMERIS-style sneaker reel from dropped sources."""
    from .shnurok.build import build_shnurok
    styles = ("classic", "bold") if style == "both" else (style,)
    outs = build_shnurok(folder, styles=styles)
    for st, path in outs.items():
        console.print(f"[green]{st}[/green] -> {path}")


def _ffprobe_duration(path: Path) -> float:
    """Get video/audio duration in seconds via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _words_per_minute(transcript: Transcript) -> float | None:
    if transcript.duration <= 0:
        return None
    return round((len(transcript.words) / transcript.duration) * 60, 1)


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:40]


_VOICEOVER_MARKER_RE = re.compile(r"\{\{\s*(?:pause\s*:|slow|/slow|product_break)", re.IGNORECASE)
_LEGACY_PAUSE_RE = re.compile(r"<<\s*\d+(?:[.,]\d+)?\s*>>")
_SHORT_SENTENCE_RE = re.compile(r"([^.!?{}]{1,42}[.!?])(\s+)(?=[А-ЯЁA-Z0-9«])")
_STRUCTURAL_BEAT_RE = re.compile(
    r"\b(Перв(?:ое|ый)|Втор(?:ое|ой)|Третье|Четвертое|И нет|А именно)\.",
    re.IGNORECASE,
)


def _annotate_voiceover_text(text: str) -> str:
    """Apply deterministic voiceover pacing markers for new scripts."""
    if _VOICEOVER_MARKER_RE.search(text) or _LEGACY_PAUSE_RE.search(text):
        return text

    annotated = _STRUCTURAL_BEAT_RE.sub(r"\1. {{pause:0.35}}", text)

    def add_short_sentence_pause(match: re.Match[str]) -> str:
        sentence = match.group(1)
        words = re.findall(r"[\wёЁ]+", sentence, flags=re.UNICODE)
        if 1 <= len(words) <= 4:
            return f"{sentence} {{pause:0.25}}{match.group(2)}"
        return match.group(0)

    annotated = _SHORT_SENTENCE_RE.sub(add_short_sentence_pause, annotated)
    annotated = re.sub(
        r"(до\s+)(\d+\s+[а-яё]+(?:\s+за\s+[а-яё]+)?)",
        r"\1{{slow}}\2{{/slow}}",
        annotated,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s{2,}", " ", annotated).strip()


def _pick_from_table(asset_type: str) -> str:
    """Show asset table and prompt user to pick one by ID."""
    meta = list_assets(asset_type)  # type: ignore[arg-type]
    if not meta:
        console.print(f"[red]No {asset_type} found. Run 'scan-assets' first.[/red]")
        raise typer.Exit(1)

    table = Table(title=f"Available {asset_type}", show_lines=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("ID", style="cyan")
    table.add_column("Duration", justify="right")
    table.add_column("Tags")
    table.add_column("Description")

    items = sorted(meta.items())
    for i, (asset_id, entry) in enumerate(items, 1):
        table.add_row(str(i), asset_id, f"{entry.duration:.1f}s",
                      ", ".join(entry.tags), entry.description)
    console.print(table)

    while True:
        choice = typer.prompt(f"Enter ID or number").strip()
        if choice in meta:
            return choice
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(items):
                return items[idx][0]
        except ValueError:
            pass
        console.print("[yellow]Not found, try again[/yellow]")


def _asset_sort_key(asset_id: str) -> tuple[str, int | str]:
    match = re.match(r"^(.*?)(\d+)$", asset_id)
    if not match:
        return asset_id, asset_id
    prefix, number = match.groups()
    return prefix, int(number)


def _prompt_choice(label: str, options: list[str], default: str | None = None) -> str:
    options_display = "/".join(options)
    fallback = default or options[0]
    while True:
        value = typer.prompt(f"{label} [{options_display}]", default=fallback).strip().lower()
        if value in options:
            return value
        console.print(f"[yellow]Choose one of: {options_display}[/yellow]")


@app.command(name="scan-assets")
def scan_assets(
    asset_type: str = typer.Argument(..., help="hooks | broll | ctas"),
    tag: Optional[list[str]] = typer.Option(None, "--tag", "-t", help="Tags to apply to ALL new assets"),
):
    """
    Scan an asset folder, auto-detect durations, ask for per-clip tags & description.
    Merges into existing _meta.json without overwriting already-described clips.
    """
    valid = {"hooks", "broll", "ctas"}
    if asset_type not in valid:
        console.print(f"[red]asset_type must be one of: {valid}[/red]")
        raise typer.Exit(1)

    folder = ASSETS_DIR / asset_type
    meta_path = folder / "_meta.json"
    existing = load_asset_meta(asset_type) if meta_path.exists() else {}

    video_files = sorted([
        f for ext in ("*.mp4", "*.MP4", "*.mov", "*.MOV", "*.m4v")
        for f in folder.glob(ext)
    ])
    new_files = [f for f in video_files if f.stem not in existing]

    if not new_files:
        console.print(f"[green]All files in {asset_type}/ already indexed.[/green]")
        return

    console.print(Panel(f"Found [bold]{len(new_files)}[/bold] new file(s) in [cyan]{asset_type}/[/cyan]"))

    for f in new_files:
        duration = _ffprobe_duration(f)
        console.print(f"\n[bold cyan]{f.name}[/bold cyan]  [dim]{duration:.1f}s[/dim]")

        raw_tags = typer.prompt("  Tags (comma-separated, e.g. утро,кофе)", default="")
        file_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        if tag:
            file_tags = list(set(file_tags + list(tag)))

        description = typer.prompt("  Short description (or Enter to skip)", default="")

        asset_id = f.stem  # e.g. hook_001
        existing[asset_id] = AssetEntry(
            file=f.name,
            duration=round(duration, 2),
            tags=file_tags,
            description=description,
        )
        console.print(f"  [green]✓[/green] Indexed as [cyan]{asset_id}[/cyan]")

    save_asset_meta(asset_type, existing)
    console.print(f"\n[bold green]✓ Saved {meta_path}[/bold green]")


@app.command(name="scan-broll")
def scan_broll(
    rebuild_ingest: bool = typer.Option(
        False,
        "--rebuild-ingest",
        help="Recreate canonical ingest cache even if normalized files already exist.",
    ),
    no_ingest: bool = typer.Option(
        False,
        "--no-ingest",
        help="Only refresh _meta.json and durations; skip normalized ingest cache generation.",
    ),
):
    """Scan assets/broll, refresh metadata, and optionally build canonical ingest cache."""
    cfg = _load_config()
    summary = scan_broll_library(cfg, rebuild_ingest=rebuild_ingest, no_ingest=no_ingest)

    console.print(
        Panel(
            "\n".join(
                [
                    f"Scanned: {summary.scanned}",
                    f"New clips: {summary.new_assets}",
                    f"Updated clips: {summary.updated_assets}",
                    f"Normalized ingest clips: {summary.normalized_assets}",
                    f"Still need annotation: {summary.needs_annotation}",
                    f"Saved meta: {summary.meta_path}",
                ]
            ),
            title="B-roll Scan Complete",
        )
    )


@app.command(name="annotate-broll")
def annotate_broll(
    ids: Optional[str] = typer.Option(
        None,
        "--ids",
        help="Comma-separated b-roll IDs to annotate. Default: all pending clips.",
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        help="Stop after annotating N clips.",
    ),
    all_clips: bool = typer.Option(
        False,
        "--all",
        help="Include clips that already have required fields filled.",
    ),
):
    """Interactively fill the first-pass b-roll annotation fields."""
    cfg = _load_config()
    meta = load_asset_meta("broll")
    selected_ids = {item.strip() for item in ids.split(",")} if ids else None

    items = sorted(meta.items(), key=lambda item: _asset_sort_key(item[0]))
    processed = 0

    for asset_id, entry in items:
        if selected_ids is not None and asset_id not in selected_ids:
            continue
        if not all_clips and not entry.needs_annotation:
            continue
        if limit is not None and processed >= limit:
            break

        clip_path = get_asset_path("broll", asset_id, prefer_ingest=False)
        console.print(
            Panel(
                "\n".join(
                    [
                        f"ID: {asset_id}",
                        f"File: {clip_path}",
                        f"Duration: {entry.duration:.2f}s",
                        "Open the clip in Quick Look or VLC, then answer the prompts below.",
                    ]
                ),
                title="Annotate B-roll",
            )
        )

        entry.shot_scale = _prompt_choice(
            "shot_scale",
            ["wide", "medium", "close", "detail"],
            entry.shot_scale,
        )
        entry.subject_kind = _prompt_choice(
            "subject_kind",
            ["self", "people", "hands", "computer", "road", "city", "nature", "object", "product"],
            entry.subject_kind,
        )
        energy_default = str(entry.energy) if entry.energy is not None else "2"
        entry.energy = int(_prompt_choice("energy", ["1", "2", "3"], energy_default))
        entry.scene_group = typer.prompt(
            "scene_group",
            default=entry.scene_group or "",
        ).strip() or None

        default_tags = ",".join(entry.tags)
        raw_tags = typer.prompt("tags (comma-separated, optional)", default=default_tags).strip()
        entry.tags = [tag.strip() for tag in raw_tags.split(",") if tag.strip()]
        entry.description = typer.prompt(
            "description (optional)",
            default=entry.description,
        ).strip()

        meta[asset_id] = entry
        processed += 1

    save_asset_meta("broll", meta)
    summary = scan_broll_library(cfg, no_ingest=True)
    console.print(
        Panel(
            "\n".join(
                [
                    f"Annotated this run: {processed}",
                    f"Still need annotation: {summary.needs_annotation}",
                    f"Saved meta: {summary.meta_path}",
                ]
            ),
            title="Annotation Saved",
        )
    )


@app.command(name="preview-broll")
def preview_broll(
    asset_id: str = typer.Argument(..., help="B-roll ID, for example broll_1"),
    frames: int = typer.Option(4, "--frames", min=4, max=9, help="Number of frames in the contact sheet."),
):
    """Generate a contact sheet and lightweight mp4 preview for a b-roll clip."""
    cfg = _load_config()
    contact_sheet_path, preview_mp4_path = render_broll_preview(
        asset_id,
        Path(cfg.output_dir),
        frames=frames,
    )
    console.print(
        Panel(
            "\n".join(
                [
                    f"Contact sheet: {contact_sheet_path}",
                    f"Preview video: {preview_mp4_path}",
                ]
            ),
            title=f"B-roll Preview: {asset_id}",
        )
    )


@app.command(name="new")
def new_script(
    tts_preset: Optional[str] = typer.Option(
        None,
        "--tts-preset",
        help="Save and build this script with voice model preset: v2 or v3.",
    ),
):
    """
    Interactively create a new video script and optionally start building immediately.
    No JSON editing required.
    """
    console.print(Panel("[bold]New video script[/bold]", subtitle="Press Ctrl-C to cancel"))

    # --- voiceover text ---
    console.print("\n[bold]1. Voiceover text[/bold] (the narration — no hook, no CTA)")
    voiceover_text = typer.prompt("  Text").strip()
    if not voiceover_text:
        console.print("[red]Voiceover text cannot be empty.[/red]")
        raise typer.Exit(1)
    voiceover_text = _annotate_voiceover_text(voiceover_text)

    # --- slug ---
    today = date.today().isoformat()
    default_slug = f"{today}-{_slugify(voiceover_text[:30])}"
    slug = typer.prompt("\n[bold]2. Slug[/bold] (filename-safe ID)", default=default_slug).strip()

    # --- hook ---
    console.print("\n[bold]3. Pick a hook[/bold]")
    hook_id = _pick_from_table("hooks")

    # --- cta ---
    console.print("\n[bold]4. Pick a CTA[/bold]")
    cta_id = _pick_from_table("ctas")

    # --- broll strategy ---
    console.print("\n[bold]5. B-roll strategy[/bold]")
    console.print("  [cyan]1[/cyan] auto    — random from all b-roll")
    console.print("  [cyan]2[/cyan] by_tags — filter by tags")
    console.print("  [cyan]3[/cyan] manual  — choose specific clips")
    strat_choice = typer.prompt("  Choice", default="1").strip()
    strat_map = {"1": "auto", "2": "by_tags", "3": "manual"}
    broll_strategy = strat_map.get(strat_choice, "auto")

    broll_tags: list[str] = []
    broll_ids: Optional[list[str]] = None

    if broll_strategy == "by_tags":
        raw = typer.prompt("  Tags to prefer (comma-separated)").strip()
        broll_tags = [t.strip() for t in raw.split(",") if t.strip()]
    elif broll_strategy == "manual":
        console.print("\n  Pick b-roll clips (enter IDs one by one, empty line to finish):")
        meta = list_assets("broll")  # type: ignore[arg-type]
        table = Table(show_lines=False)
        table.add_column("ID", style="cyan"); table.add_column("Duration"); table.add_column("Tags")
        for aid, e in sorted(meta.items()):
            table.add_row(aid, f"{e.duration:.1f}s", ", ".join(e.tags))
        console.print(table)
        picked: list[str] = []
        while True:
            clip_id = typer.prompt("  Add clip ID (Enter to finish)", default="").strip()
            if not clip_id:
                break
            if clip_id in meta:
                picked.append(clip_id)
                console.print(f"  [green]+[/green] {clip_id}")
            else:
                console.print(f"  [yellow]Unknown ID '{clip_id}', skipped[/yellow]")
        broll_ids = picked or None

    requested_tts_preset = _normalize_tts_preset(tts_preset)
    cfg = _with_tts_preset(_load_config(), requested_tts_preset)
    subtitle_style = _pick_subtitle_style(cfg)

    # --- build script ---
    script_data = VideoScript(
        slug=slug,
        hook_id=hook_id,
        cta_id=cta_id,
        voiceover_text=voiceover_text,
        tts_preset=requested_tts_preset,
        broll_strategy=broll_strategy,
        broll_tags_preferred=broll_tags,
        broll_ids_override=broll_ids,
        subtitle_style=subtitle_style,
    )

    scripts_dir = Path(__file__).parent.parent / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    out_path = scripts_dir / f"{slug}.json"
    out_path.write_text(script_data.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")

    console.print(f"\n[bold green]✓ Script saved:[/bold green] {out_path}")

    # --- optionally start building ---
    if typer.confirm("\nBuild the video now?", default=True):
        _run_build(script_data, cfg, output_suffix=requested_tts_preset)


def _run_build(
    vs: VideoScript,
    cfg: Config,
    output_suffix: Optional[str] = None,
    version: Optional[str] = None,
) -> None:
    """Shared build logic used by `build` and `new`."""
    t0 = time.time()
    output_slug = _output_slug(vs.slug, output_suffix)
    console.rule(f"[bold]Building: {output_slug}[/bold]")

    try:
        validate_assets(vs.slug, vs.hook_id, vs.cta_id)
    except (ValueError, FileNotFoundError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    style_id = _subtitle_style_id(vs, cfg)
    style = _subtitle_style(vs, cfg)
    hook_style_id = _hook_subtitle_style_id(cfg, style_id)
    hook_style = style if hook_style_id == style_id else cfg.subtitle_styles[hook_style_id]

    build_base = Path(cfg.output_dir) / output_slug
    previous_dir = latest_version_dir(build_base)
    out_dir = versioned_dir(build_base, version=version)
    seed_reusable(previous_dir, out_dir, ["voiceover.mp3", "*.hash", "transcript.json"])
    (
        vo_path,
        transcript,
        tail_silence_sec,
        product_break_after_token,
        voiceover_plan,
    ) = _prepare_voiceover_assets(vs, cfg, out_dir)

    # 4. transcribe hook & cta (cached as sidecars) + generate their .ass files
    hook_entry = load_asset_meta("hooks")[vs.hook_id]
    cta_entry = load_asset_meta("ctas")[vs.cta_id]
    hook_cfg = _with_hook_asset_accents(cfg, hook_entry)
    hook_path = get_asset_path("hooks", vs.hook_id)
    cta_path = get_asset_path("ctas", vs.cta_id)
    look_profile_id = _auto_look_profile(vs, cfg, hook_path)

    console.log("[blue]→[/blue] Transcribing hook audio (cached per asset)...")
    if hook_entry.transcript_text:
        hook_transcript = transcribe_asset(
            hook_path,
            model_size=cfg.whisper.transcribe_model,
            transcript_text=hook_entry.transcript_text,
        )
    else:
        hook_transcript = transcribe_asset(
            hook_path,
            model_size=cfg.whisper.transcribe_model,
        )
    cta_context_text = cta_entry.transcript_text or cta_entry.description or None
    console.log("[blue]→[/blue] Building hook montage...")
    hook_montage = render_hook_montage(
        hook_path,
        hook_transcript,
        hook_style,
        out_dir,
        hook_cfg,
        cta_context_text=cta_context_text,
        script_overlay_id=vs.hook_overlay_id,
    )
    hook_render_path = hook_montage.video_path
    hook_ass = hook_montage.subtitle_path
    hook_transcript_for_diagnostics = hook_montage.transcript
    hook_subtitle_warnings = hook_montage.subtitle_warnings

    console.log("[blue]→[/blue] Transcribing cta audio (cached per asset)...")
    if cta_entry.transcript_text:
        cta_transcript = transcribe_asset(
            cta_path,
            model_size=cfg.whisper.transcribe_model,
            transcript_text=cta_entry.transcript_text,
        )
    else:
        cta_transcript = transcribe_asset(
            cta_path,
            model_size=cfg.whisper.transcribe_model,
        )
    _enforce_render_guardrails(
        cfg,
        hook_montage=hook_montage.diagnostics,
        cta_duration_sec=_normalized_cta_duration_sec(cta_path, cta_transcript, cfg),
    )
    cta_ass = out_dir / "cta_subs.ass"
    cta_subtitle_warnings: list[str] = []
    transcript_to_ass(
        cta_transcript,
        style,
        cta_ass,
        safe_box=cfg.subtitle_safe_box,
        cfg=cfg,
        layout_warnings=cta_subtitle_warnings,
        section="cta",
    )

    # 5. b-roll rhythm planning + section build
    render_plan = plan_broll(
        strategy=vs.broll_strategy,
        target_duration=max(
            transcript.duration + tail_silence_sec,
            _ffprobe_duration(vo_path),
        ),
        cfg=cfg,
        transcript=transcript,
        product_insert=vs.product_insert,
        product_break_after_token=product_break_after_token,
        tags=vs.broll_tags_preferred or None,
        ids_override=vs.broll_ids_override,
    )
    render_plan = render_plan.model_copy(
        update={
            "look_profile": look_profile_id,
            "tts_model_id": cfg.tts.model_id,
            "tts_preset": _infer_tts_preset(cfg),
            "tts_voice_id": os.environ.get("ELEVENLABS_VOICE_ID"),
            "tts_generation_mode": cfg.tts.generation_mode,
            "tts_markup_dialect": cfg.tts.markup_dialect,
            "tts_output_format": cfg.tts.output_format,
            "tts_calls_count": len(split_product_breaks(voiceover_plan.tts_text)),
            "tts_used_voice_settings_override": cfg.tts.generation_mode != "direct",
            "tts_inline_break_tags_count": len(
                re.findall(r"<break\s+time=", voiceover_plan.tts_text, flags=re.IGNORECASE)
            ),
            "tts_dictionary_locators": [
                f"{locator.pronunciation_dictionary_id}:{locator.version_id}"
                for locator in cfg.tts.pronunciation_dictionary_locators
            ],
            "tts_words_per_minute": _words_per_minute(transcript),
            "body_alignment_model": cfg.whisper.align_model,
            "hook_transcribe_model": cfg.whisper.transcribe_model,
            "cta_transcribe_model": cfg.whisper.transcribe_model,
            "hook_montage": hook_montage.diagnostics,
        }
    )
    render_plan = build_block_plan(render_plan, cfg)
    music_path = _resolve_music_path(cfg)
    ass_path = out_dir / "subtitles.ass"
    subtitle_layout_warnings: list[str] = []
    transcript_to_ass(
        transcript,
        style,
        ass_path,
        safe_box=cfg.subtitle_safe_box,
        cfg=cfg,
        layout_warnings=subtitle_layout_warnings,
        caption_plan_path=out_dir / "caption_plan.json" if style.caption_mode == "editorial" else None,
        section="body",
    )
    if subtitle_layout_warnings:
        render_plan.warnings.extend(subtitle_layout_warnings)
    if hook_subtitle_warnings:
        render_plan.warnings.extend(f"hook: {warning}" for warning in hook_subtitle_warnings)
    if cta_subtitle_warnings:
        render_plan.warnings.extend(f"cta: {warning}" for warning in cta_subtitle_warnings)
    render_plan = render_plan.model_copy(
        update={
            "subtitle_diagnostics": [
                subtitle_diagnostics(
                    transcript,
                    style,
                    label="body",
                    layout_warnings=subtitle_layout_warnings,
                ),
                subtitle_diagnostics(
                    hook_transcript_for_diagnostics,
                    hook_style,
                    label="hook",
                    layout_warnings=hook_subtitle_warnings,
                ),
                subtitle_diagnostics(
                    cta_transcript,
                    style,
                    label="cta",
                    layout_warnings=cta_subtitle_warnings,
                ),
            ]
        }
    )
    _append_hook_cta_duration_warnings(render_plan, hook_render_path, cta_path)

    broll_section = build_broll_section(
        render_plan,
        vo_path,
        ass_path,
        out_dir,
        cfg,
        section_name="broll_section_clean.mp4",
    )

    if music_path:
        console.log(f"[blue]→[/blue] Using background music: {music_path.name}")
    else:
        console.log("[yellow]→[/yellow] No music configured/found for edit profile, skipping")
    music_start_sec = _music_start_sec(cfg, hook_montage.diagnostics)
    render_plan = render_plan.model_copy(
        update={
            "music_start_sec": round(music_start_sec, 3) if music_path else None,
            "music_fade_in_sec": cfg.audio.music_fade_in_sec if music_path else None,
        }
    )

    # 7. final concat with subtitles burnt onto hook & cta, + background music
    final = concat_final(
        hook_render_path, broll_section, cta_path, out_dir, cfg,
        hook_ass=hook_ass, cta_ass=cta_ass,
        cta_transcript=cta_transcript,
        music_path=music_path,
        look_profile=look_profile_id,
        music_start_sec=music_start_sec,
    )
    first_frame_report = inspect_first_frames(out_dir / "final.mp4")
    render_plan = render_plan.model_copy(
        update={
            "first_frame_black": first_frame_report.is_black,
            "first_frame_mean_luma": round(first_frame_report.mean_luma, 3),
            "first_frame_black_pixel_ratio": round(first_frame_report.black_pixel_ratio, 4),
        }
    )
    render_plan_path = out_dir / "render_plan.json"
    render_plan_path.write_text(
        render_plan.model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8",
    )
    console.log(f"[green]✓[/green] Render plan saved: {render_plan_path}")
    diagnostics_path = out_dir / "render_diagnostics.md"
    write_render_diagnostics(render_plan, transcript, diagnostics_path)
    console.log(f"[green]✓[/green] Render diagnostics saved: {diagnostics_path}")
    if first_frame_report.is_black:
        raise RuntimeError(
            "First-frame validation failed: first frames are black "
            f"(mean_luma={first_frame_report.mean_luma:.2f}, "
            f"black_pixel_ratio={first_frame_report.black_pixel_ratio:.3f})"
        )

    elapsed = time.time() - t0
    console.rule(f"[bold green]Done in {elapsed:.1f}s → {final}[/bold green]")


def _prepare_voiceover_assets(
    vs: VideoScript,
    cfg: Config,
    out_dir: Path,
) -> tuple[Path, Transcript, float, int | None, VoiceoverTextPlan]:
    """Build final voiceover audio and align it."""
    voiceover_plan = prepare_voiceover_text(
        vs.voiceover_text,
        markup_dialect=cfg.tts.markup_dialect,
    )
    tail_silence_sec = (
        vs.voiceover_tail_silence_sec
        if vs.voiceover_tail_silence_sec is not None
        else cfg.audio.voiceover_tail_silence_sec
    )

    vo_path = out_dir / "voiceover.mp3"
    synthesize(
        voiceover_plan.tts_text,
        vo_path,
        settings=cfg.tts,
        tail_silence_sec=tail_silence_sec,
        product_break_pause_sec=cfg.product_insert.marker_pause_sec,
        lead_silence_sec=cfg.audio.voiceover_lead_silence_sec,
    )

    transcript_path = out_dir / "transcript.json"
    transcript = align_to_text(
        vo_path,
        voiceover_plan.align_text,
        transcript_path,
        model_size=cfg.whisper.align_model,
        display_text=voiceover_plan.display_text,
    )

    return (
        vo_path,
        transcript,
        tail_silence_sec,
        voiceover_plan.product_break_after_token,
        voiceover_plan,
    )


if __name__ == "__main__":
    app()
