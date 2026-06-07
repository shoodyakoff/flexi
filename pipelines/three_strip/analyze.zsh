#!/bin/zsh
# analyze.zsh <clipnum> [interval_s] [scene_thresh]
# Pipeline to understand a long clip before authoring an episode:
#  1) dense timestamped contact sheet (orientation-corrected)
#  2) ffmpeg scene-change detection -> candidate "beat" boundaries
#
# Config via env vars (sensible relative defaults so it works from a clone):
#   RAW     source folder of clips        (default ./raw)
#   OUTDIR  where sheets/scenes land       (default ./output/analyze)
#   NEED180 space list of clips to rotate 180 (default empty — iPhone tags lie,
#           so verify orientation by eye and list the upside-down ones here)
#   FONT    label font                     (default macOS Arial)
# Example: RAW=raw/d33 NEED180="4901 4903" zsh analyze.zsh 4907
set -e
setopt null_glob
RAW="${RAW:-./raw}"
OUTDIR="${OUTDIR:-./output/analyze}"
FONT="${FONT:-/System/Library/Fonts/Supplemental/Arial.ttf}"
NEED180="${NEED180:-}"
num=$1; interval=${2:-2.5}; thr=${3:-0.4}
out="$OUTDIR/$num"; mkdir -p "$out"
cd "$RAW"
f="IMG_${num}.MOV"; [[ -f "$f" ]] || f="IMG_${num}.mov"
[[ -f "$f" ]] || { echo "no clip $num in $RAW"; exit 1; }

# orientation: same verified rule as the build (NEED180 from env)
need180() { case " ${NEED180} " in *" $1 "*) return 0;; *) return 1;; esac }
pre=""; if need180 "$num"; then pre="hflip,vflip,"; fi

dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")
echo "clip $num  dur=${dur}s  interval=${interval}s  flip=$([[ -n $pre ]] && echo yes || echo no)"

# 1) dense frames
i=0; t=$(awk "BEGIN{printf \"%.2f\", $interval/2}")
while (( $(awk "BEGIN{print ($t < $dur)}") )); do
  idx=$(printf "%03d" $i)
  ffmpeg -nostdin -noautorotate -y -ss "$t" -i "$f" -vframes 1 \
    -vf "${pre}scale=360:202,drawtext=fontfile=${FONT}:text='${t}s':fontcolor=yellow:fontsize=22:box=1:boxcolor=black@0.7:x=4:y=4" \
    "$out/fr_${idx}.jpg" -loglevel error
  i=$((i+1)); t=$(awk "BEGIN{printf \"%.2f\", $t + $interval}")
done
# tile: 6 cols
ffmpeg -nostdin -y -framerate 1 -pattern_type glob -i "$out/fr_*.jpg" -vf "tile=6x6:margin=5:padding=3:color=0x303030" "$out/sheet_%02d.png" -loglevel error

# 2) scene detection
echo ">> scene cuts (thr=$thr):"
ffmpeg -nostdin -noautorotate -i "$f" -vf "select='gt(scene,${thr})',showinfo" -an -f null - 2>&1 \
  | grep -oE "pts_time:[0-9.]+" | cut -d: -f2 | awk '{printf "%.2f ", $1} END{print ""}' | tee "$out/scenes.txt"
echo ">> frames: $(ls "$out"/fr_*.jpg | wc -l | tr -d ' ')  sheet(s): $(ls "$out"/sheet_*.png)"
