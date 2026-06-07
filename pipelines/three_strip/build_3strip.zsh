#!/bin/zsh
# ============================================================================
# 3-STRIP REEL ENGINE  —  reusable, config-driven.
#   usage:  zsh build_3strip.zsh episodes/<name>.conf
# Reads an episode config (clip lists + params), renders the 3-strip cascade
# reel, optional bed cascade-out outro, music, and auto-runs the QA layer.
# Encodes RULES.md: HDR->SDR, per-clip 180-flip, column-aligned cascade,
# no-3-same, max-1-screen, demo->usage (authored in config), bed bookend.
# ============================================================================
set -e; setopt null_glob
HERE=${0:A:h}
CONF="${1:?usage: build_3strip.zsh <episode.conf>}"; CONF=${CONF:A}
source "$CONF"

# ---- defaults ----
: ${LEAD_top:=0.5}; : ${LEAD_bot:=1.0}; : ${ENDLEN:=0}
: ${MUSIC_START:=0}; : ${MUSIC_VOL:=0.7}; : ${MAX_DUR:=45}
: ${CRF:=19}; : ${PRESET:=medium}
: ${OUTRO_TOP_VIS:=1.0}; : ${OUTRO_BOT_VIS:=1.8}
: ${WORK:="$OUT/work_$NAME"}
mkdir -p "$WORK/band"; cd "$RAW"

TONE="zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
SCALE="scale=1080:632:force_original_aspect_ratio=increase,crop=1080:632,fps=30,setsar=1"
need180() { case " ${NEED180} " in *" $1 "*) return 0;; *) return 1;; esac }

clipfile() { local f="IMG_$1.MOV"; [[ -f "$f" ]] || f="IMG_$1.mov"; echo "$f"; }
render_band() { # num in dur out   (param-keyed name -> safe cache)
  [[ -s "$4" ]] && return 0
  local f=$(clipfile "$1") pre=""
  need180 "$1" && pre="hflip,vflip,"
  ffmpeg -nostdin -noautorotate -y -ss "$2" -t "$3" -i "$f" \
    -vf "${pre}${TONE},${SCALE},format=yuv420p" -an -r 30 \
    -c:v libx264 -crf $CRF -preset $PRESET -video_track_timescale 30000 "$4" -loglevel error
}
blkband() { ffmpeg -nostdin -y -f lavfi -i color=c=black:s=1080x632:r=30 -t "$1" \
  -c:v libx264 -crf $CRF -preset $PRESET -video_track_timescale 30000 "$2" -loglevel error; }
build_band() { # name leaddur "num in dur"...
  local name=$1 lead=$2; shift 2
  local txt="$WORK/${name}.txt"; : > "$txt"
  (( lead > 0 )) && { blkband "$lead" "$WORK/blk_${name}.mp4"; echo "file '$WORK/blk_${name}.mp4'" >> "$txt"; }
  local i=0
  for l in "$@"; do set -- ${(z)l}
    local out="$WORK/band/${name}_$(printf '%02d' $i)_${1}_${2//./p}-${3//./p}.mp4"
    render_band "$1" "$2" "$3" "$out"; echo "file '$out'" >> "$txt"; i=$((i+1)); done
  ffmpeg -nostdin -y -f concat -safe 0 -i "$txt" -c copy "$WORK/${name}.mp4" -loglevel error
}
bandsum() { local s=0; for l in "$@"; do set -- ${(z)l}; s=$(awk "BEGIN{print $s+$3}"); done; echo $s }

# ---- sanity: no duplicate clips (rule: без повторов) ----
dups=$(print -l ${MID} ${TOP} ${BOT} ${OUTRO_TOP} ${OUTRO_BOT} ${OUTRO_CTR} | awk '{print $1}' | sort | uniq -d)
[[ -n "$dups" ]] && echo "WARN duplicate clips used: $dups"

echo "== [$NAME] render bands (mid ${#MID}, top ${#TOP}, bot ${#BOT}) =="
build_band mid 0          "${MID[@]}"
build_band top $LEAD_top  "${TOP[@]}"
build_band bot $LEAD_bot  "${BOT[@]}"
mtot=$(bandsum "${MID[@]}"); ttot=$(bandsum "${TOP[@]}"); btot=$(bandsum "${BOT[@]}")
trim=$(awk "BEGIN{m=$mtot;t=$ttot+$LEAD_top;b=$btot+$LEAD_bot;x=m;if(t<x)x=t;if(b<x)x=b;printf \"%.2f\",x}")

echo "== body composite (trim ${trim}s) =="
ffmpeg -nostdin -y -i "$WORK/mid.mp4" -i "$WORK/top.mp4" -i "$WORK/bot.mp4" -filter_complex "\
color=c=black:s=1080x1920:r=30:d=60[bg];\
[bg][0:v]overlay=0:644:eof_action=repeat[m1];[m1][1:v]overlay=0:0:eof_action=repeat[m2];\
[m2][2:v]overlay=0:1288:eof_action=repeat,format=yuv420p[v]" \
  -map "[v]" -t "$trim" -r 30 -c:v libx264 -crf $CRF -preset $PRESET \
  -color_primaries bt709 -color_trc bt709 -colorspace bt709 -video_track_timescale 30000 "$WORK/body.mp4" -loglevel error

SEG="$WORK/body.mp4"; total=$trim; outro_start=$trim
if (( ENDLEN > 0 )) && [[ -n "$OUTRO_CTR" ]]; then
  echo "== bed cascade-out outro (all appear -> top off -> bot off -> center holds) =="
  rm -f "$WORK"/end_top.mp4 "$WORK"/end_bot.mp4
  set -- ${(z)OUTRO_TOP}; render_band $1 $2 $OUTRO_TOP_VIS "$WORK/end_top.mp4"
  set -- ${(z)OUTRO_BOT}; render_band $1 $2 $OUTRO_BOT_VIS "$WORK/end_bot.mp4"
  set -- ${(z)OUTRO_CTR}; oc=$1; oci=$2; ocf=$(clipfile $oc); ocpre=""; need180 $oc && ocpre="hflip,vflip,"
  ffmpeg -nostdin -noautorotate -y -ss "$oci" -i "$ocf" \
    -vf "${ocpre}${TONE},${SCALE},tpad=stop_mode=clone:stop_duration=10,format=yuv420p" -t "$ENDLEN" \
    -an -r 30 -c:v libx264 -crf $CRF -preset $PRESET -video_track_timescale 30000 "$WORK/end_ctr.mp4" -loglevel error
  padT=$(awk "BEGIN{printf \"%.2f\", $ENDLEN-$OUTRO_TOP_VIS}"); padB=$(awk "BEGIN{printf \"%.2f\", $ENDLEN-$OUTRO_BOT_VIS}")
  blkband "$padT" "$WORK/padT.mp4"; blkband "$padB" "$WORK/padB.mp4"
  printf "file '%s'\nfile '%s'\n" "$WORK/end_top.mp4" "$WORK/padT.mp4" > "$WORK/etop.txt"
  printf "file '%s'\nfile '%s'\n" "$WORK/end_bot.mp4" "$WORK/padB.mp4" > "$WORK/ebot.txt"
  ffmpeg -nostdin -y -f concat -safe 0 -i "$WORK/etop.txt" -c copy "$WORK/etop.mp4" -loglevel error
  ffmpeg -nostdin -y -f concat -safe 0 -i "$WORK/ebot.txt" -c copy "$WORK/ebot.mp4" -loglevel error
  ffmpeg -nostdin -y -i "$WORK/etop.mp4" -i "$WORK/end_ctr.mp4" -i "$WORK/ebot.mp4" -filter_complex "\
color=c=black:s=1080x1920:r=30:d=${ENDLEN}[bg];\
[bg][0:v]overlay=0:0[a];[a][1:v]overlay=0:644[b];[b][2:v]overlay=0:1288,format=yuv420p[v]" \
    -map "[v]" -t "$ENDLEN" -r 30 -c:v libx264 -crf $CRF -preset $PRESET \
    -color_primaries bt709 -color_trc bt709 -colorspace bt709 -video_track_timescale 30000 "$WORK/ending.mp4" -loglevel error
  printf "file '%s'\nfile '%s'\n" "$WORK/body.mp4" "$WORK/ending.mp4" > "$WORK/full.txt"
  ffmpeg -nostdin -y -f concat -safe 0 -i "$WORK/full.txt" -c copy "$WORK/full.mp4" -loglevel error
  SEG="$WORK/full.mp4"; total=$(awk "BEGIN{printf \"%.2f\", $trim+$ENDLEN}")
fi

(( $(awk "BEGIN{print ($total>$MAX_DUR+0.2)}") )) && echo "WARN total ${total}s > MAX_DUR ${MAX_DUR}s"

OUTFILE="$OUT/${NAME}.mp4"
FADEOUT=$(awk -v d="$total" 'BEGIN{printf "%.2f", d-0.8}')
echo "== mux music ($(basename "$MUSIC") from ${MUSIC_START}s) =="
ffmpeg -nostdin -y -i "$SEG" -ss "$MUSIC_START" -stream_loop -1 -i "$MUSIC" -filter_complex "\
[0:v]fade=t=in:st=0:d=0.3,fade=t=out:st=${FADEOUT}:d=0.8[v];\
[1:a]volume=${MUSIC_VOL},afade=t=in:st=0:d=0.4,afade=t=out:st=${FADEOUT}:d=0.8[a]" \
  -map "[v]" -map "[a]" -t "$total" -c:v libx264 -crf $CRF -preset $PRESET \
  -color_primaries bt709 -color_trc bt709 -colorspace bt709 -c:a aac -b:a 192k -movflags +faststart \
  "$OUTFILE" -loglevel error
echo "== DONE: $OUTFILE (${total}s) =="

# ---- auto QA ----
# Prefer a repo-local venv if present, else fall back to python3 on PATH.
PY="${PYTHON:-}"
[[ -z "$PY" && -x "$HERE/../../.venv/bin/python3" ]] && PY="$HERE/../../.venv/bin/python3"
[[ -z "$PY" ]] && PY="$(command -v python3)"
[[ -n "$PY" ]] && { echo "== QA =="; "$PY" "$HERE/qa.py" "$OUTFILE" "$MAX_DUR" 1.5 "$outro_start" || true; }
