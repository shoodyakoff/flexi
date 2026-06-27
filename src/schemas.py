from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

MotionProfileName = Literal["off", "calm", "balanced", "dynamic", "aggressive"]
LookProfileName = Literal["neutral", "warm", "punchy", "punchy_bright"]
RhythmProfileName = Literal["provocative_soft"]
BrollStrategy = Literal["auto", "by_tags", "manual"]
TTSPreset = Literal["v2", "v3"]
MusicStartMode = Literal["full_reel", "after_hook_effect"]
HookOverlayPlacement = Literal[
    "right_mid",
    "left_mid",
    "top_right",
    "top_left",
    "bottom_right",
    "bottom_left",
]
SceneType = Literal[
    "life", "work", "computer", "street", "road",
    "city", "nature", "product", "screen", "ambient",
]
ShotScale = Literal["wide", "medium", "close", "detail"]
SubjectKind = Literal[
    "self", "people", "hands", "computer", "road",
    "city", "nature", "object", "product",
]
SequenceRole = Literal["establish", "action", "detail", "transition"]
RhythmRole = Literal["support", "detail", "anchor"]
TrimPolicy = Literal["flex", "keep_full"]
TransitionKind = Literal[
    "cut",
    "fade",  # alias for crossfade/dissolve
    "dissolve",
    "dipblack",
    "dipwhite",
    "wipeleft",
    "wiperight",
    "wipeup",
    "wipedown",
    "slideleft",
    "slideright",
    "slideup",
    "slidedown",
]
BlockType = Literal["explain", "demo", "transition", "turn"]
SfxKind = Literal[
    "anchor_accent",
    "transition_soft",
    "product_insert",
    "key_punch",
    "cta_leadin",
]
SfxGenerator = Literal[
    "accent_soft",
    "transition_soft",
    "brand_ping",
    "punch_click",
    "cta_lift",
]
MotionPreset = Literal[
    "static",
    "zoom_in_soft",
    "zoom_out_soft",
    "drift_left",
    "drift_right",
    "push_up",
    "push_down",
]


class ProductInsert(BaseModel):
    """Brand/product insert inside the b-roll section."""

    file: str
    anchor_root: Optional[str] = None
    lead_in_sec: float = 1.5
    snap_window_sec: float = 0.7


class VideoScript(BaseModel):
    slug: str
    hook_id: str
    cta_id: str
    voiceover_text: str
    caption_text: Optional[str] = None
    pinned_comment: Optional[str] = None
    tts_preset: Optional[TTSPreset] = None
    voiceover_tail_silence_sec: Optional[float] = None
    broll_strategy: BrollStrategy = "auto"
    broll_tags_preferred: list[str] = Field(default_factory=list)
    broll_ids_override: Optional[list[str]] = None
    subtitle_style: Optional[str] = None
    subtitle_accent_words: list[str] = Field(default_factory=list)
    subtitle_accent_phrases: list[str] = Field(default_factory=list)
    look_profile: Optional[LookProfileName] = None
    product_insert: Optional[ProductInsert] = None
    # Backward compatibility for current scripts.
    highlight_overlay: Optional[ProductInsert] = None
    # Manual override for the hook icon picker: ID from assets/hook_overlays/_meta.json.
    hook_overlay_id: Optional[str] = None

    @field_validator("broll_ids_override")
    @classmethod
    def check_manual_has_ids(cls, v, info):
        if info.data.get("broll_strategy") == "manual" and not v:
            raise ValueError("broll_ids_override required when broll_strategy is 'manual'")
        return v

    @model_validator(mode="after")
    def upgrade_legacy_fields(self) -> "VideoScript":
        if self.product_insert is None and self.highlight_overlay is not None:
            self.product_insert = self.highlight_overlay
        return self


class AssetEntry(BaseModel):
    file: str
    duration: float
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    transcript_text: Optional[str] = None
    accent_keywords: list[str] = Field(default_factory=list)
    # Hook overlay icons: morphological roots (e.g. "оффер", "приглашен") that
    # vote for this icon when found in hook/CTA voiceover.
    anchors: list[str] = Field(default_factory=list)

    # v3 b-roll metadata. Hooks/CTAs simply ignore these optional fields.
    scene_type: Optional[SceneType] = None
    scene_group: Optional[str] = None
    shot_scale: Optional[ShotScale] = None
    subject_kind: Optional[SubjectKind] = None
    sequence_role: Optional[SequenceRole] = None
    energy: Optional[int] = None
    motion_allowed: Optional[bool] = None
    allowed_motion_presets: list[MotionPreset] = Field(default_factory=list)
    motion_intensity_cap: Optional[float] = None
    effect_profile_override: Optional[str] = None
    trim_policy: TrimPolicy = "flex"
    weight: Optional[float] = None
    ingest_file: Optional[str] = None
    needs_annotation: bool = False

    @field_validator("energy")
    @classmethod
    def validate_energy(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v not in (1, 2, 3):
            raise ValueError("energy must be 1, 2, or 3")
        return v

    @field_validator("motion_intensity_cap")
    @classmethod
    def validate_motion_intensity_cap(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not 0 <= v <= 1.5:
            raise ValueError("motion_intensity_cap must be between 0 and 1.5")
        return v

    def missing_broll_annotation_fields(self) -> list[str]:
        required = ("shot_scale", "subject_kind", "energy", "scene_group")
        missing: list[str] = []
        for field_name in required:
            if getattr(self, field_name) in (None, "", []):
                missing.append(field_name)
        return missing


class Word(BaseModel):
    word: str
    start: float
    end: float


class Transcript(BaseModel):
    words: list[Word]
    full_text: str
    duration: float


class BRollClip(BaseModel):
    asset_id: str
    file: str
    start: float
    end: float


class AssetSnapshot(BaseModel):
    """Compact snapshot of editor metadata used during runtime planning."""

    duration: float
    scene_type: Optional[SceneType] = None
    scene_group: Optional[str] = None
    shot_scale: Optional[ShotScale] = None
    subject_kind: Optional[SubjectKind] = None
    sequence_role: Optional[SequenceRole] = None
    energy: Optional[int] = None
    motion_allowed: Optional[bool] = None
    allowed_motion_presets: list[MotionPreset] = Field(default_factory=list)
    motion_intensity_cap: Optional[float] = None
    effect_profile_override: Optional[str] = None
    trim_policy: TrimPolicy = "flex"
    weight: Optional[float] = None


class RenderPlanClip(BaseModel):
    sequence_index: int
    asset_id: str
    file: str
    source_start: float
    source_end: float
    timeline_start: float
    timeline_end: float
    duration_sec: float
    rhythm_role: Optional[RhythmRole] = None
    placement_reason: str
    block_kind: Optional[BlockType] = None
    block_reason: Optional[str] = None
    motion_preset: Optional[MotionPreset] = None
    motion_strength: Optional[float] = None
    motion_reason: Optional[str] = None
    is_product_insert: bool = False
    asset_snapshot: Optional[AssetSnapshot] = None


class RenderPlanTransition(BaseModel):
    from_sequence_index: int
    to_sequence_index: int
    timeline_start: float
    duration_sec: float
    kind: TransitionKind
    reason: str
    beat_time: Optional[float] = None
    beat_delta_sec: Optional[float] = None
    snapped_to_beat: bool = False


class RenderPlanSfxEvent(BaseModel):
    sequence_index: int
    kind: SfxKind
    generator: SfxGenerator
    timeline_start: float
    duration_sec: float
    gain_db: float
    reason: str
    beat_time: Optional[float] = None
    beat_delta_sec: Optional[float] = None
    snapped_to_beat: bool = False
    sample_path: Optional[str] = None


class RenderPlanBlock(BaseModel):
    sequence_index: int
    kind: BlockType
    timeline_start: float
    timeline_end: float
    clip_indices: list[int] = Field(default_factory=list)
    reason: str


class RenderPlanBeatMarker(BaseModel):
    sequence_index: int
    beat_index: int
    timeline_start: float


class HookMontageTimeRange(BaseModel):
    start: float
    end: float


class HookMontageOverlayDiagnostics(BaseModel):
    keyword: str
    word: str
    file: str
    placement: HookOverlayPlacement
    word_start_sec: float
    word_end_sec: float
    start_sec: float
    peak_sec: float
    end_sec: float
    width_px: int
    opacity: float = 1.0


class HookMontageSfxDiagnostics(BaseModel):
    kind: str
    file: str
    timeline_start_sec: float
    trim_start_sec: float = 0.0
    duration_sec: float
    gain_db: float
    peak_target_sec: Optional[float] = None


class HookMontageDiagnostics(BaseModel):
    enabled: bool = False
    source_duration_sec: float = 0.0
    speech_duration_sec: float = 0.0
    tail_padding_sec: float = 0.0
    final_duration_sec: float = 0.0
    effect_end_sec: float = 0.0
    kept_ranges: list[HookMontageTimeRange] = Field(default_factory=list)
    removed_ranges: list[HookMontageTimeRange] = Field(default_factory=list)
    overlays: list[HookMontageOverlayDiagnostics] = Field(default_factory=list)
    sfx_events: list[HookMontageSfxDiagnostics] = Field(default_factory=list)
    motion_events: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RenderPlan(BaseModel):
    edit_profile: RhythmProfileName = "provocative_soft"
    strategy: BrollStrategy
    look_profile: Optional[LookProfileName] = None
    target_duration_sec: float
    planned_duration_sec: float
    soft_rules: bool
    clips: list[RenderPlanClip]
    blocks: list[RenderPlanBlock] = Field(default_factory=list)
    beat_markers: list[RenderPlanBeatMarker] = Field(default_factory=list)
    transitions: list[RenderPlanTransition] = Field(default_factory=list)
    sfx_events: list[RenderPlanSfxEvent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    product_insert_reason: Optional[str] = None
    tts_model_id: Optional[str] = None
    tts_preset: Optional[str] = None
    tts_voice_id: Optional[str] = None
    tts_generation_mode: Optional[str] = None
    tts_markup_dialect: Optional[str] = None
    tts_output_format: Optional[str] = None
    tts_calls_count: Optional[int] = None
    tts_used_voice_settings_override: Optional[bool] = None
    tts_inline_break_tags_count: Optional[int] = None
    tts_dictionary_locators: list[str] = Field(default_factory=list)
    tts_words_per_minute: Optional[float] = None
    body_alignment_model: Optional[str] = None
    hook_transcribe_model: Optional[str] = None
    cta_transcribe_model: Optional[str] = None
    subtitle_diagnostics: list["SubtitleTrackDiagnostics"] = Field(default_factory=list)
    hook_montage: Optional[HookMontageDiagnostics] = None
    music_start_sec: Optional[float] = None
    music_fade_in_sec: Optional[float] = None
    first_frame_black: Optional[bool] = None
    first_frame_mean_luma: Optional[float] = None
    first_frame_black_pixel_ratio: Optional[float] = None


class SubtitleTrackDiagnostics(BaseModel):
    label: str
    transcript_tokens: int
    rendered_tokens: int
    missing_tokens: int
    short_event_count: int
    double_content_word_chunks: int
    auto_fit_events: int = 0


class SubtitleStyle(BaseModel):
    font: str
    size: int
    primary_color: str
    outline_color: str
    outline: int
    margin_v: int
    shadow: int = 0
    blur: float = 0.0
    accent_font: Optional[str] = None
    accent_size: Optional[int] = None
    accent_color: Optional[str] = None
    accent_outline_color: Optional[str] = None
    accent_outline: Optional[int] = None
    accent_shadow: Optional[int] = None
    accent_keywords: list[str] = Field(default_factory=list)
    accent_phrases: list[str] = Field(default_factory=list)
    accent_numbers: bool = False
    accent_long_word_min_chars: int = 0
    caption_mode: str = "word"
    animation: str = "none"
    pop_enter_ms: int = 0
    pop_settle_ms: int = 0
    pop_start_scale: int = 100
    pop_overshoot_scale: int = 100
    pop_final_scale: int = 100
    ghost_preflash: bool = False
    ghost_alpha: int = 90
    ghost_offset_px: int = 2
    uppercase: bool = True
    strip_punctuation: bool = True
    max_lines: int = 2
    min_font_scale: float = 0.72
    split_long_words: bool = True
    long_word_split_min_chars: int = 14
    max_words_per_chunk: int = 1
    max_chunk_duration_sec: float = 1.2
    max_chunk_gap_sec: float = 0.18
    min_display_duration_sec: float = 0.28
    min_event_duration_warning_sec: float = 0.12
    end_hold_sec: float = 0.06
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    attach_short_tokens: bool = True
    attach_numbers: bool = True
    forbid_two_content_words: bool = True

    @model_validator(mode="after")
    def validate_style(self) -> "SubtitleStyle":
        if self.size <= 0:
            raise ValueError("subtitle style size must be positive")
        if self.accent_size is not None and self.accent_size <= 0:
            raise ValueError("subtitle style accent_size must be positive")
        if self.accent_long_word_min_chars < 0:
            raise ValueError("subtitle style accent_long_word_min_chars must be non-negative")
        if self.caption_mode not in {"word", "editorial"}:
            raise ValueError("subtitle style caption_mode must be one of: word, editorial")
        if self.animation not in {"none", "pop"}:
            raise ValueError("subtitle style animation must be one of: none, pop")
        if self.pop_enter_ms < 0 or self.pop_settle_ms < 0:
            raise ValueError("subtitle style pop durations must be non-negative")
        for scale in (self.pop_start_scale, self.pop_overshoot_scale, self.pop_final_scale):
            if scale <= 0:
                raise ValueError("subtitle style pop scales must be positive")
        if not 0 <= self.ghost_alpha <= 255:
            raise ValueError("subtitle style ghost_alpha must be between 0 and 255")
        if self.ghost_offset_px < 0:
            raise ValueError("subtitle style ghost_offset_px must be non-negative")
        if not 0.4 <= self.min_font_scale <= 1.0:
            raise ValueError("subtitle style min_font_scale must be between 0.4 and 1.0")
        if self.long_word_split_min_chars < 6:
            raise ValueError("subtitle style long_word_split_min_chars must be at least 6")
        if self.max_lines < 1:
            raise ValueError("subtitle style max_lines must be at least 1")
        if self.max_words_per_chunk < 1:
            raise ValueError("subtitle style max_words_per_chunk must be at least 1")
        if self.max_chunk_duration_sec <= 0:
            raise ValueError("subtitle style max_chunk_duration_sec must be positive")
        if self.max_chunk_gap_sec < 0:
            raise ValueError("subtitle style max_chunk_gap_sec must be non-negative")
        if self.min_display_duration_sec <= 0:
            raise ValueError("subtitle style min_display_duration_sec must be positive")
        if self.min_event_duration_warning_sec < 0:
            raise ValueError("subtitle style min_event_duration_warning_sec must be non-negative")
        if self.end_hold_sec < 0:
            raise ValueError("subtitle style end_hold_sec must be non-negative")
        if self.fade_in_ms < 0 or self.fade_out_ms < 0:
            raise ValueError("subtitle style fade durations must be non-negative")
        return self


class SubtitleSafeBoxConfig(BaseModel):
    play_res_x: int = 1080
    play_res_y: int = 1920
    center_x: int = 540
    top_padding_px: int = 220
    bottom_padding_px: int = 460
    side_padding_px: int = 120
    # Where the running body captions sit vertically. "bottom" = lower third
    # (default, the standard reel look); "top" = upper area (use only when the
    # lower part of the frame is reserved for b-roll). Hook/CTA captions are
    # unaffected. Applies across every route that burns subtitles.
    body_caption_position: str = "bottom"

    @model_validator(mode="after")
    def validate_safe_box(self) -> "SubtitleSafeBoxConfig":
        for field_name in (
            "play_res_x",
            "play_res_y",
            "center_x",
            "top_padding_px",
            "bottom_padding_px",
            "side_padding_px",
        ):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"subtitle_safe_box.{field_name} must be non-negative")
        if self.center_x > self.play_res_x:
            raise ValueError("subtitle_safe_box.center_x must stay within play_res_x")
        if self.top_padding_px >= self.play_res_y:
            raise ValueError("subtitle_safe_box.top_padding_px must stay within play_res_y")
        if self.bottom_padding_px >= self.play_res_y:
            raise ValueError("subtitle_safe_box.bottom_padding_px must stay within play_res_y")
        return self


class FontConfig(BaseModel):
    directory: str = "./assets/fonts"
    base_family: str = "Onest"
    accent_family: Optional[str] = None
    provocative_accent_family: Optional[str] = None

    def resolved_accent_family(self) -> Optional[str]:
        return self.accent_family or self.provocative_accent_family


class EditProfile(BaseModel):
    subtitle_style: str
    look_profile: LookProfileName = "neutral"
    dark_look_profile: Optional[LookProfileName] = None
    dark_luma_threshold: float = 90.0
    rhythm_profile: RhythmProfileName = "provocative_soft"
    music_file: Optional[str] = None


class RhythmRoleBudget(BaseModel):
    min_duration_sec: float
    preferred_duration_min_sec: float
    preferred_duration_max_sec: float
    max_duration_sec: float
    shot_scales: list[ShotScale] = Field(default_factory=list)
    sequence_roles: list[SequenceRole] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_budget(self) -> "RhythmRoleBudget":
        if self.min_duration_sec <= 0:
            raise ValueError("rhythm role min_duration_sec must be positive")
        if self.max_duration_sec < self.min_duration_sec:
            raise ValueError("rhythm role max_duration_sec must be >= min_duration_sec")
        if self.preferred_duration_min_sec > self.preferred_duration_max_sec:
            raise ValueError(
                "rhythm role preferred_duration_min_sec must be <= preferred_duration_max_sec"
            )
        if self.preferred_duration_min_sec < self.min_duration_sec:
            raise ValueError(
                "rhythm role preferred_duration_min_sec must be >= min_duration_sec"
            )
        if self.preferred_duration_max_sec > self.max_duration_sec:
            raise ValueError(
                "rhythm role preferred_duration_max_sec must be <= max_duration_sec"
            )
        return self


class RhythmConfig(BaseModel):
    pattern: list[RhythmRole] = Field(
        default_factory=lambda: ["support", "support", "detail", "anchor"]
    )
    roles: dict[RhythmRole, RhythmRoleBudget] = Field(
        default_factory=lambda: {
            "support": RhythmRoleBudget(
                min_duration_sec=1.6,
                preferred_duration_min_sec=1.8,
                preferred_duration_max_sec=2.2,
                max_duration_sec=2.4,
                shot_scales=["wide", "medium"],
                sequence_roles=["establish", "action"],
            ),
            "detail": RhythmRoleBudget(
                min_duration_sec=1.0,
                preferred_duration_min_sec=1.1,
                preferred_duration_max_sec=1.5,
                max_duration_sec=1.6,
                shot_scales=["detail", "close"],
                sequence_roles=["detail", "action"],
            ),
            "anchor": RhythmRoleBudget(
                min_duration_sec=2.6,
                preferred_duration_min_sec=2.8,
                preferred_duration_max_sec=3.4,
                max_duration_sec=3.6,
                shot_scales=["medium", "wide"],
                sequence_roles=["action", "establish"],
            ),
        }
    )
    snap_tolerance_sec: float = 0.35
    phrase_gap_min_sec: float = 0.25
    contrast_window_sec: float = 9.0

    @model_validator(mode="after")
    def validate_pattern(self) -> "RhythmConfig":
        if not self.pattern:
            raise ValueError("rhythm pattern must not be empty")
        missing = [role for role in self.pattern if role not in self.roles]
        if missing:
            raise ValueError(f"rhythm pattern references undefined role(s): {missing}")
        return self


class ProductInsertRules(BaseModel):
    min_duration_sec: float = 2.0
    preferred_duration_sec: float = 3.0
    preferred_duration_min_sec: float = 2.5
    preferred_duration_max_sec: float = 4.0
    max_duration_sec: float = 4.0
    marker_pause_sec: float = 0.8
    fallback_pause_after_ratio: float = 0.55
    fallback_pause_min_gap_sec: float = 0.25
    timeline_fallback_ratio: float = 0.65

    @model_validator(mode="after")
    def validate_duration_budget(self) -> "ProductInsertRules":
        if self.min_duration_sec <= 0:
            raise ValueError("product_insert.min_duration_sec must be positive")
        if self.max_duration_sec < self.min_duration_sec:
            raise ValueError("product_insert.max_duration_sec must be >= min_duration_sec")
        if self.preferred_duration_min_sec > self.preferred_duration_max_sec:
            raise ValueError(
                "product_insert.preferred_duration_min_sec must be <= preferred_duration_max_sec"
            )
        if self.preferred_duration_max_sec > self.max_duration_sec:
            raise ValueError(
                "product_insert.preferred_duration_max_sec must be <= max_duration_sec"
            )
        if not 0 <= self.fallback_pause_after_ratio <= 1:
            raise ValueError("product_insert.fallback_pause_after_ratio must be between 0 and 1")
        if not 0 <= self.timeline_fallback_ratio <= 1:
            raise ValueError("product_insert.timeline_fallback_ratio must be between 0 and 1")
        return self


class TransitionConfig(BaseModel):
    enabled: bool = False
    kind: TransitionKind = "fade"
    duration_sec: float = 0.10
    min_clip_duration_sec: float = 0.9
    skip_product_insert: bool = True

    @model_validator(mode="after")
    def validate_transition(self) -> "TransitionConfig":
        if self.duration_sec < 0:
            raise ValueError("transition.duration_sec must be non-negative")
        if self.min_clip_duration_sec <= 0:
            raise ValueError("transition.min_clip_duration_sec must be positive")
        return self


class MotionConfig(BaseModel):
    enabled: bool = False
    provocative_every_n: int = 2
    min_clip_duration_sec: float = 1.6
    zoom_scale_delta: float = 0.05
    drift_scale_delta: float = 0.04
    vertical_scale_delta: float = 0.05

    @model_validator(mode="after")
    def validate_motion(self) -> "MotionConfig":
        if self.provocative_every_n <= 0:
            raise ValueError("motion.provocative_every_n must be positive")
        if self.min_clip_duration_sec <= 0:
            raise ValueError("motion.min_clip_duration_sec must be positive")
        for field_name in ("zoom_scale_delta", "drift_scale_delta", "vertical_scale_delta"):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"motion.{field_name} must be non-negative")
            if value > 0.15:
                raise ValueError(f"motion.{field_name} must stay conservative (<= 0.15)")
        return self


class MotionProfileConfig(BaseModel):
    provocative_every_n: int = 2
    anchor_only: bool = False
    intensity_multiplier: float = 1.0

    @model_validator(mode="after")
    def validate_motion_profile(self) -> "MotionProfileConfig":
        if self.provocative_every_n <= 0:
            raise ValueError("motion profile provocative_every_n must be positive")
        if not 0 <= self.intensity_multiplier <= 1.5:
            raise ValueError("motion profile intensity_multiplier must be between 0 and 1.5")
        return self


class LookProfileConfig(BaseModel):
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    gamma: float = 1.0
    sharpness: float = 0.0
    # Optional .cube 3D LUT applied after the eq grade for a film/brand look.
    lut_file: Optional[str] = None

    @model_validator(mode="after")
    def validate_look_profile(self) -> "LookProfileConfig":
        if not -0.15 <= self.brightness <= 0.15:
            raise ValueError("look profile brightness must be between -0.15 and 0.15")
        if not 0.8 <= self.contrast <= 1.4:
            raise ValueError("look profile contrast must be between 0.8 and 1.4")
        if not 0.8 <= self.saturation <= 1.4:
            raise ValueError("look profile saturation must be between 0.8 and 1.4")
        if not 0.8 <= self.gamma <= 1.2:
            raise ValueError("look profile gamma must be between 0.8 and 1.2")
        if not 0 <= self.sharpness <= 1.5:
            raise ValueError("look profile sharpness must be between 0 and 1.5")
        return self


class SfxConfig(BaseModel):
    enabled: bool = False
    min_interval_sec: float = 0.9
    provocative_transition_every_n: int = 1
    provocative_key_punch_every_n: int = 1
    anchor_duration_sec: float = 0.12
    transition_duration_sec: float = 0.10
    product_duration_sec: float = 0.18
    punch_duration_sec: float = 0.08
    cta_duration_sec: float = 0.16
    anchor_gain_db: float = -24.0
    transition_gain_db: float = -27.0
    product_gain_db: float = -21.0
    punch_gain_db: float = -25.0
    cta_gain_db: float = -22.0
    cta_leadin_offset_from_end_sec: float = 0.18

    @model_validator(mode="after")
    def validate_sfx(self) -> "SfxConfig":
        if self.min_interval_sec < 0:
            raise ValueError("sfx.min_interval_sec must be non-negative")
        if self.provocative_transition_every_n <= 0:
            raise ValueError("sfx.provocative_transition_every_n must be positive")
        if self.provocative_key_punch_every_n <= 0:
            raise ValueError("sfx.provocative_key_punch_every_n must be positive")
        for field_name in (
            "anchor_duration_sec",
            "transition_duration_sec",
            "product_duration_sec",
            "punch_duration_sec",
            "cta_duration_sec",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"sfx.{field_name} must be positive")
        if self.cta_leadin_offset_from_end_sec < 0:
            raise ValueError("sfx.cta_leadin_offset_from_end_sec must be non-negative")
        return self


class HookMontageTypographyConfig(BaseModel):
    style: Optional[str] = None
    base_y_px: int = 255
    accent_y_px: int = 470
    accent_size: int = 112
    accent_keywords: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_typography(self) -> "HookMontageTypographyConfig":
        if self.base_y_px < 0 or self.accent_y_px < 0:
            raise ValueError("hook_montage typography positions must be non-negative")
        if self.accent_size <= 0:
            raise ValueError("hook_montage.typography.accent_size must be positive")
        return self


class HookMontageMotionConfig(BaseModel):
    enabled: bool = True
    opening_punch_in: bool = True
    opening_duration_sec: float = 0.55
    opening_zoom_delta: float = 0.025
    keyword_punch: bool = True
    keyword_punch_duration_sec: float = 0.28
    keyword_zoom_delta: float = 0.035
    riser_zoom: bool = False
    riser_zoom_delta: float = 0.055
    riser_zoom_out_duration_sec: float = 2.0
    riser_zoom_in_duration_sec: float = 0.16

    @model_validator(mode="after")
    def validate_hook_motion(self) -> "HookMontageMotionConfig":
        if self.opening_duration_sec <= 0:
            raise ValueError("hook_montage.motion.opening_duration_sec must be positive")
        if self.keyword_punch_duration_sec <= 0:
            raise ValueError("hook_montage.motion.keyword_punch_duration_sec must be positive")
        if self.riser_zoom_out_duration_sec <= 0:
            raise ValueError("hook_montage.motion.riser_zoom_out_duration_sec must be positive")
        if self.riser_zoom_in_duration_sec <= 0:
            raise ValueError("hook_montage.motion.riser_zoom_in_duration_sec must be positive")
        for field_name in ("opening_zoom_delta", "keyword_zoom_delta"):
            value = getattr(self, field_name)
            if not 0 <= value <= 0.15:
                raise ValueError(f"hook_montage.motion.{field_name} must be between 0 and 0.15")
        if not 0 <= self.riser_zoom_delta <= 0.45:
            raise ValueError("hook_montage.motion.riser_zoom_delta must be between 0 and 0.45")
        return self


class HookMontageTransitionConfig(BaseModel):
    enabled: bool = True
    duration_sec: float = 0.32
    fade_in_sec: float = 0.06
    color: str = "0xffb14a"
    opacity: float = 0.88

    @model_validator(mode="after")
    def validate_hook_transition(self) -> "HookMontageTransitionConfig":
        if self.duration_sec <= 0:
            raise ValueError("hook_montage.transition.duration_sec must be positive")
        if self.fade_in_sec < 0:
            raise ValueError("hook_montage.transition.fade_in_sec must be non-negative")
        if self.fade_in_sec >= self.duration_sec:
            raise ValueError("hook_montage.transition.fade_in_sec must be < duration_sec")
        if not 0 < self.opacity <= 1:
            raise ValueError("hook_montage.transition.opacity must be between 0 and 1")
        if not self.color:
            raise ValueError("hook_montage.transition.color must not be empty")
        return self


class HookMontageSfxConfig(BaseModel):
    riser: Optional[str] = None
    riser_peak_offset_sec: float = 2.02
    riser_gain_db: float = -6.0
    swoosh: Optional[str] = None
    swoosh_gain_db: float = -8.0
    max_sfx_events: int = 2
    min_swoosh_gap_from_peak_sec: float = 0.50

    @model_validator(mode="after")
    def validate_hook_sfx(self) -> "HookMontageSfxConfig":
        if self.riser_peak_offset_sec < 0:
            raise ValueError("hook_montage.sfx.riser_peak_offset_sec must be non-negative")
        if self.max_sfx_events < 0:
            raise ValueError("hook_montage.sfx.max_sfx_events must be non-negative")
        if self.min_swoosh_gap_from_peak_sec < 0:
            raise ValueError("hook_montage.sfx.min_swoosh_gap_from_peak_sec must be non-negative")
        if self.riser_gain_db > 0 or self.swoosh_gain_db > 0:
            raise ValueError("hook_montage SFX gains must not exceed 0 dB")
        if self.riser_gain_db < -18 or self.swoosh_gain_db < -20:
            raise ValueError("hook_montage SFX gains are too low to stay audible over voice")
        return self


class HookMontageOverlaysConfig(BaseModel):
    """Auto-picked hook overlay icons. The picker scores icons from
    `assets/hook_overlays/_meta.json` against hook+CTA voiceover and inserts
    the winner; rendering params below are shared across all icons."""

    max_events: int = 1
    min_score: float = 1.0
    cta_weight: float = 0.5
    accent_bonus: float = 0.5
    placement: HookOverlayPlacement = "bottom_right"
    width_px: int = 300
    opacity: float = 1.35
    enter_sec: float = 0.14
    exit_sec: float = 0.12
    display_duration_sec: float = 2.00

    @model_validator(mode="after")
    def validate_hook_overlays(self) -> "HookMontageOverlaysConfig":
        if self.max_events < 0:
            raise ValueError("hook_montage.overlays.max_events must be non-negative")
        if self.min_score < 0:
            raise ValueError("hook_montage.overlays.min_score must be non-negative")
        if self.cta_weight < 0 or self.accent_bonus < 0:
            raise ValueError("hook_montage.overlays scoring weights must be non-negative")
        if self.width_px <= 0:
            raise ValueError("hook_montage.overlays.width_px must be positive")
        if not 0 < self.opacity <= 2.0:
            raise ValueError("hook_montage.overlays.opacity must be between 0 and 2")
        if self.enter_sec < 0 or self.exit_sec < 0:
            raise ValueError("hook_montage.overlays fade durations must be non-negative")
        if self.display_duration_sec <= 0:
            raise ValueError("hook_montage.overlays.display_duration_sec must be positive")
        return self


class HookMontageConfig(BaseModel):
    enabled: bool = True
    target_min_sec: float = 3.0
    target_max_sec: float = 8.9
    silence_gap_cut_sec: float = 0.75
    speech_lead_padding_sec: float = 0.12
    speech_padding_sec: float = 0.5
    tail_padding_sec: float = 0.5
    typography: HookMontageTypographyConfig = HookMontageTypographyConfig()
    motion: HookMontageMotionConfig = HookMontageMotionConfig()
    transition: HookMontageTransitionConfig = HookMontageTransitionConfig()
    sfx: HookMontageSfxConfig = HookMontageSfxConfig()
    overlays: HookMontageOverlaysConfig = HookMontageOverlaysConfig()

    @model_validator(mode="after")
    def validate_hook_montage(self) -> "HookMontageConfig":
        if self.target_min_sec <= 0 or self.target_max_sec <= 0:
            raise ValueError("hook_montage target durations must be positive")
        if self.target_max_sec < self.target_min_sec:
            raise ValueError("hook_montage.target_max_sec must be >= target_min_sec")
        if self.silence_gap_cut_sec < 0:
            raise ValueError("hook_montage.silence_gap_cut_sec must be non-negative")
        if self.speech_lead_padding_sec < 0:
            raise ValueError("hook_montage.speech_lead_padding_sec must be non-negative")
        if self.speech_padding_sec < 0:
            raise ValueError("hook_montage.speech_padding_sec must be non-negative")
        if self.tail_padding_sec < 0:
            raise ValueError("hook_montage.tail_padding_sec must be non-negative")
        return self


class BeatConfig(BaseModel):
    enabled: bool = False
    provocative_bpm: float = 128.0
    subdivision: int = 1
    snap_window_sec: float = 0.12
    cut_snap_window_sec: float = 0.08
    min_clip_duration_sec: float = 0.9
    max_markers: int = 512

    @model_validator(mode="after")
    def validate_beat(self) -> "BeatConfig":
        if self.provocative_bpm <= 0:
            raise ValueError("beat.provocative_bpm must be positive")
        if self.subdivision <= 0:
            raise ValueError("beat.subdivision must be positive")
        if self.snap_window_sec < 0:
            raise ValueError("beat.snap_window_sec must be non-negative")
        if self.cut_snap_window_sec < 0:
            raise ValueError("beat.cut_snap_window_sec must be non-negative")
        if self.min_clip_duration_sec <= 0:
            raise ValueError("beat.min_clip_duration_sec must be positive")
        if self.max_markers <= 0:
            raise ValueError("beat.max_markers must be positive")
        return self


class AnnotationConfig(BaseModel):
    ingest_dir: str = "assets/broll_ingest"
    normalize_new_clips: bool = True
    pilot_target_count: int = 20
    default_weight: float = 1.0
    required_fields: list[str] = Field(
        default_factory=lambda: [
            "shot_scale",
            "subject_kind",
            "energy",
            "scene_group",
        ]
    )
    motion_presets_by_shot_scale: dict[ShotScale, list[MotionPreset]] = Field(
        default_factory=lambda: {
            "wide": [
                "zoom_in_soft",
                "zoom_out_soft",
                "drift_left",
                "drift_right",
                "push_up",
                "push_down",
            ],
            "medium": [
                "zoom_in_soft",
                "zoom_out_soft",
                "drift_left",
                "drift_right",
            ],
            "close": ["static"],
            "detail": ["static"],
        }
    )


class RenderGuardrailsConfig(BaseModel):
    fail_on_long_hook: bool = True
    fail_on_missing_hook_accent: bool = True
    fail_on_long_cta: bool = True
    cta_target_max_sec: float = 6.0
    cta_soft_tail_sec: float = 0.35

    @model_validator(mode="after")
    def validate_render_guardrails(self) -> "RenderGuardrailsConfig":
        if self.cta_target_max_sec <= 0:
            raise ValueError("render_guardrails.cta_target_max_sec must be positive")
        if self.cta_soft_tail_sec < 0:
            raise ValueError("render_guardrails.cta_soft_tail_sec must be non-negative")
        return self


class BrollRotationConfig(BaseModel):
    recent_history_size: int = 8
    recent_start_window: int = 4
    recent_start_penalty: float = 18.0
    recent_anywhere_penalty: float = 6.0

    @model_validator(mode="after")
    def validate_broll_rotation(self) -> "BrollRotationConfig":
        if self.recent_history_size < 0 or self.recent_start_window < 0:
            raise ValueError("broll_rotation history/window values must be non-negative")
        if self.recent_start_penalty < 0 or self.recent_anywhere_penalty < 0:
            raise ValueError("broll_rotation penalties must be non-negative")
        return self


class AudioConfig(BaseModel):
    sample_rate: int
    voiceover_gain_db: float
    broll_audio_gain_db: float
    music_gain_db: float = -20
    music_fade_in_sec: float = 1.0
    music_fade_out_sec: float = 1.5
    music_start_mode: MusicStartMode = "full_reel"
    # Auto-ducking: приглушать музыку под голос (sidechaincompress).
    music_ducking: bool = True
    music_duck_threshold: float = 0.03  # уровень речи, с которого жмём (0..1)
    music_duck_ratio: float = 8.0  # сила сжатия музыки
    music_duck_attack_ms: float = 20.0  # как быстро музыка уходит вниз
    music_duck_release_ms: float = 300.0  # как быстро возвращается после фразы
    loudnorm_target_lufs: float = -16
    loudnorm_true_peak: float = -1.5
    loudnorm_lra: float = 11
    voiceover_tail_silence_sec: float = 0.1
    voiceover_lead_silence_sec: float = 0.0


class VideoConfig(BaseModel):
    resolution: str
    fps: int
    codec: str
    crf: int
    crf_intermediate: int = 18


class WhisperConfig(BaseModel):
    align_model: str = "large-v3"
    transcribe_model: str = "large-v3"


class TTSPronunciationDictionaryLocator(BaseModel):
    pronunciation_dictionary_id: str
    version_id: str


class TTSConfig(BaseModel):
    model_id: str = "eleven_multilingual_v2"
    output_format: str = "mp3_44100_192"
    language_code: Optional[str] = "ru"
    apply_text_normalization: Literal["auto", "on", "off"] = "auto"
    generation_mode: Literal["direct"] = "direct"
    markup_dialect: Literal["v2", "v3"] = "v2"
    speed: float = 1.0
    pronunciation_dictionary_locators: list[TTSPronunciationDictionaryLocator] = Field(
        default_factory=list
    )

    @field_validator("speed")
    @classmethod
    def validate_speed(cls, v: float) -> float:
        if not 0.7 <= v <= 1.2:
            raise ValueError("tts.speed must be between 0.7 and 1.2")
        return v

    @model_validator(mode="after")
    def validate_tts(self) -> "TTSConfig":
        if len(self.pronunciation_dictionary_locators) > 3:
            raise ValueError("tts.pronunciation_dictionary_locators supports at most 3 locators")
        return self


class TitleOverlayConfig(BaseModel):
    # Animated "challenge title" clip: text on a BLACK background, full-frame
    # 1080x1920, pre-positioned top-left. It is overlaid onto the start of the
    # video for its own duration. The black background is keyed out (colorkey)
    # and the title fades out at its end so it disappears gracefully.
    colorkey_color: str = "0x000000"
    colorkey_similarity: float = 0.10
    colorkey_blend: float = 0.08
    fade_out_sec: float = 0.36
    # Default challenge-title clip, overlaid automatically when --auto-title is
    # set (used for challenge-day videos so the title is added without a reminder).
    # None = no default; an explicit --title always wins.
    default_title: Optional[str] = None


class Config(BaseModel):
    output_dir: str
    video: VideoConfig
    audio: AudioConfig
    fonts: FontConfig = FontConfig()
    edit_profile: EditProfile
    rhythm_profiles: dict[RhythmProfileName, RhythmConfig] = Field(default_factory=dict)
    motion_profiles: dict[MotionProfileName, MotionProfileConfig] = Field(default_factory=dict)
    look_profiles: dict[LookProfileName, LookProfileConfig] = Field(default_factory=dict)
    product_insert: ProductInsertRules = ProductInsertRules()
    transition: TransitionConfig = TransitionConfig()
    motion: MotionConfig = MotionConfig()
    sfx: SfxConfig = SfxConfig()
    hook_montage: HookMontageConfig = HookMontageConfig()
    render_guardrails: RenderGuardrailsConfig = RenderGuardrailsConfig()
    broll_rotation: BrollRotationConfig = BrollRotationConfig()
    beat: BeatConfig = BeatConfig()
    subtitle_safe_box: SubtitleSafeBoxConfig = SubtitleSafeBoxConfig()
    title_overlay: TitleOverlayConfig = TitleOverlayConfig()
    annotation: AnnotationConfig = AnnotationConfig()
    subtitle_styles: dict[str, SubtitleStyle]
    subtitle_corrections: dict[str, str] = Field(default_factory=dict)
    whisper: WhisperConfig = WhisperConfig()
    tts: TTSConfig = TTSConfig()
