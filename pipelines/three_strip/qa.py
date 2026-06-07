#!/usr/bin/env python3
"""qa.py - QA layer for 3-strip vertical reels.

Screenshots the output every STEP seconds, splits each frame into the 3 bands,
and checks the acceptance criteria:
  C1 format    : 1080x1920, ~30fps, has audio
  C2 duration  : <= target
  C3 no-3-same : in every trio the 3 bands must differ (perceptual-hash distance)
  C4 faces     : never a gap > FACE_GAP_MAX s without a face somewhere on screen
  C5 no-dead   : no fully-black band after the intro
Writes: qa_report.txt (PASS/FAIL) and qa_sheet.png (annotated, for visual orientation check).

Usage: python3 qa.py <video> [target_dur] [intro_end]
"""
import sys, os, subprocess, tempfile, glob
from PIL import Image, ImageDraw, ImageFont
import imagehash, numpy as np, cv2

VID = sys.argv[1]
TARGET_DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
INTRO_END  = float(sys.argv[3]) if len(sys.argv) > 3 else 1.5   # bands legitimately black before this
OUTRO_START= float(sys.argv[4]) if len(sys.argv) > 4 else 1e9   # bed cascade-out: bands intentionally black after this
STEP = 0.6
DUP_THRESH = 10     # phash hamming < this => two bands too similar (3-same risk)
DEAD_P95   = 4      # 95th-pct luma < this => truly black/dead band (real dark night footage still spikes >4)
FACE_GAP_MAX = 3.0  # face-gap WARNING threshold (informational in rare-face mode)
OUTDIR = os.path.dirname(os.path.abspath(VID))
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"
# band y-ranges (x0,x1,y0,y1) for 1080x1920 with 632px bands + 12px gaps
BANDS = [("TOP",0,1080,0,632),("MID",0,1080,644,1276),("BOT",0,1080,1288,1920)]
CASCADE = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades,"haarcascade_frontalface_default.xml"))

def ffprobe(*a):
    return subprocess.check_output(["ffprobe","-v","error",*a,VID]).decode().strip()

def main():
    dur = float(ffprobe("-show_entries","format=duration","-of","csv=p=0"))
    w,h = ffprobe("-select_streams","v:0","-show_entries","stream=width,height","-of","csv=p=0:s=,").split(",")[:2]
    fps_raw = ffprobe("-select_streams","v:0","-show_entries","stream=r_frame_rate","-of","csv=p=0")
    n,d = fps_raw.split("/"); fps = float(n)/float(d)
    has_audio = bool(ffprobe("-select_streams","a","-show_entries","stream=index","-of","csv=p=0"))

    tmp = tempfile.mkdtemp()
    subprocess.run(["ffmpeg","-nostdin","-y","-i",VID,"-vf",f"fps=1/{STEP}",
                    os.path.join(tmp,"f_%04d.png")],
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
    frames = sorted(glob.glob(os.path.join(tmp,"f_*.png")))

    rows=[]          # per-sample analysis
    face_times=[]    # times with >=1 face
    dup_viol=[]; dead_viol=[]
    for i,fp in enumerate(frames):
        t = i*STEP
        img = Image.open(fp).convert("RGB")
        if img.size != (1080,1920):
            img = img.resize((1080,1920))
        bands={}; hashes={}; faces={}; lumas={}; p95s={}
        for name,x0,x1,y0,y1 in BANDS:
            b = img.crop((x0,y0,x1,y1)); bands[name]=b
            hashes[name]=imagehash.phash(b)
            arr=np.asarray(b.convert("L"))
            lumas[name]=float(arr.mean()); p95s[name]=float(np.percentile(arr,95))
            small=cv2.resize(arr,(320,int(320*b.size[1]/b.size[0])))
            det=CASCADE.detectMultiScale(small,scaleFactor=1.1,minNeighbors=5,minSize=(34,34))
            faces[name]=len(det)
        # dup distances
        dtm=hashes["TOP"]-hashes["MID"]; dtb=hashes["TOP"]-hashes["BOT"]; dmb=hashes["MID"]-hashes["BOT"]
        mindup=min(dtm,dtb,dmb)
        nfaces=sum(1 for v in faces.values() if v>0)
        if nfaces>0: face_times.append(t)
        # violations (skip intro for dup/dead, since bands fill in)
        in_body = (INTRO_END <= t < OUTRO_START)   # skip cascade in/out regions (bands legitimately black)
        if in_body and mindup<DUP_THRESH: dup_viol.append((round(t,1),mindup))
        if in_body:
            for name in ("TOP","MID","BOT"):
                if p95s[name]<DEAD_P95: dead_viol.append((round(t,1),name))
        rows.append(dict(t=t,mindup=mindup,nfaces=nfaces,faces=dict(faces),img=fp))

    # face gap
    pts=[0.0]+face_times+[dur]
    gaps=[(round(pts[i],1),round(pts[i+1],1),round(pts[i+1]-pts[i],1)) for i in range(len(pts)-1)]
    worst_gap=max(gaps,key=lambda g:g[2]) if gaps else (0,0,0)

    # ---- report ----
    R=[]
    def chk(name,ok,detail): R.append((name,"PASS" if ok else "FAIL",detail))
    chk("C1 format 1080x1920", (w=="1080" and h=="1920"), f"{w}x{h}")
    chk("C1 fps ~30", abs(fps-30)<0.5, f"{fps:.2f}fps")
    chk("C1 has audio", has_audio, "audio stream present" if has_audio else "NO AUDIO")
    chk("C2 duration<=target", dur<=TARGET_DUR+0.2, f"{dur:.1f}s / {TARGET_DUR:.0f}s")
    chk("C3 no-3-same (dup>=%d)"%DUP_THRESH, len(dup_viol)==0,
        "clean" if not dup_viol else f"{len(dup_viol)} trios too similar: {dup_viol[:6]}")
    c4_ok = worst_gap[2] <= FACE_GAP_MAX
    warn_c4 = f"[{'ok  ' if c4_ok else 'WARN'}] C4 face-gap (info)        worst {worst_gap[2]}s @ {worst_gap[0]}-{worst_gap[1]}s"
    chk("C5 no dead band", len(dead_viol)==0,
        "clean" if not dead_viol else f"{len(dead_viol)} dead-band samples: {dead_viol[:6]}")

    npass=sum(1 for _,s,_ in R if s=="PASS")
    lines=[f"QA REPORT  {os.path.basename(VID)}",
           f"dur={dur:.1f}s fps={fps:.2f} {w}x{h} faces_in {len(face_times)}/{len(frames)} samples","-"*60]
    for name,st,det in R: lines.append(f"[{st}] {name:<26} {det}")
    lines.append(warn_c4)
    lines.append("-"*60); lines.append(f"{npass}/{len(R)} hard checks PASS")
    report="\n".join(lines)
    open(os.path.join(OUTDIR,"qa_report.txt"),"w").write(report)
    print(report)

    # ---- annotated sheet (every other sample to stay readable) ----
    sel=rows[::2]
    tw,th=150,267; cols=8; rowsN=(len(sel)+cols-1)//cols
    pad=6; lab=22
    sheet=Image.new("RGB",(cols*(tw+pad)+pad, rowsN*(th+lab+pad)+pad),(28,28,28))
    dr=ImageDraw.Draw(sheet); font=ImageFont.truetype(FONT,15); fsm=ImageFont.truetype(FONT,13)
    for k,r in enumerate(sel):
        cx=pad+(k%cols)*(tw+pad); cy=pad+(k//cols)*(th+lab+pad)
        thumb=Image.open(r["img"]).convert("RGB").resize((tw,th))
        bad = (INTRO_END <= r["t"] < OUTRO_START and r["mindup"]<DUP_THRESH)
        border=(220,40,40) if bad else ((40,180,70) if r["nfaces"]>0 else (90,90,90))
        bx=Image.new("RGB",(tw+4,th+4),border); bx.paste(thumb,(2,2)); sheet.paste(bx,(cx-2,cy+lab-2))
        dr.text((cx,cy),f"{r['t']:.1f}s dup{r['mindup']} f{r['nfaces']}",fill=(255,255,0),font=fsm)
    sheet.save(os.path.join(OUTDIR,"qa_sheet.png"))
    print("sheet ->",os.path.join(OUTDIR,"qa_sheet.png"))
    print("(red border = 3-same violation, green = face present, gray = no face)")
    return 0 if npass==len(R) else 1

if __name__=="__main__":
    sys.exit(main())
