import shutil, subprocess
from pathlib import Path
import pytest

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")


def test_compose_hook_geometry(tmp_path):
    from src.shnurok.style import load_style
    from src.shnurok.cutout import cutout_graphic
    from src.shnurok.titles import hook_titles_ass
    from src.shnurok.matte import person_matte
    from src.shnurok.hook_compose import compose_hook
    import numpy as np; from PIL import Image
    # synthetic TH clip (2s gray), graphic (grey box on white), fonts dir = assets/fonts
    th = tmp_path / "th.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=1080x1920:d=2:r=30",
                    "-c:v", "libx264", str(th)], check=True)
    gimg = tmp_path / "g.png"
    a = np.full((200, 300, 3), 255, np.uint8); a[60:140, 90:210] = (120, 120, 120); Image.fromarray(a).save(gimg)
    car, sh = tmp_path / "car.png", tmp_path / "sh.png"; cutout_graphic(gimg, car, sh)
    s = load_style("classic")
    fa = tmp_path / "h.ass"; hook_titles_ass([(0.05, 2.0, "НАДОЕЛИ", 150, 310)], (0.5, 1.8, "МАШИНЫ?", 300, 640), s, fa)
    matte = person_matte(th, 0.0, 2.0, tmp_path / "m.mp4")
    out = compose_hook(th, 0.0, 2.0, fa, tmp_path / "h.behind.ass", matte, car, sh,
                        (0.5, 1.8), s, "assets/fonts", tmp_path / "hook.mp4")
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height", "-of", "csv=p=0", str(out)], capture_output=True, text=True)
    assert "1080,1920" in r.stdout


def test_compose_hook_no_behind(tmp_path):
    # Plain hook: no text-behind-subject, no matte — should skip the alphamerge/overlay
    # layers entirely and still produce a valid 1080x1920 composite.
    from src.shnurok.style import load_style
    from src.shnurok.cutout import cutout_graphic
    from src.shnurok.titles import hook_titles_ass
    from src.shnurok.hook_compose import compose_hook
    import numpy as np; from PIL import Image
    th = tmp_path / "th.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=1080x1920:d=2:r=30",
                    "-c:v", "libx264", str(th)], check=True)
    gimg = tmp_path / "g.png"
    a = np.full((200, 300, 3), 255, np.uint8); a[60:140, 90:210] = (120, 120, 120); Image.fromarray(a).save(gimg)
    car, sh = tmp_path / "car.png", tmp_path / "sh.png"; cutout_graphic(gimg, car, sh)
    s = load_style("classic")
    fa = tmp_path / "h.ass"; hook_titles_ass([(0.05, 2.0, "НАДОЕЛИ", 150, 310)], None, s, fa)
    out = compose_hook(th, 0.0, 2.0, fa, None, None, car, sh,
                        (0.5, 1.8), s, "assets/fonts", tmp_path / "hook_plain.mp4")
    assert out.exists()
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height", "-of", "csv=p=0", str(out)], capture_output=True, text=True)
    assert "1080,1920" in r.stdout
