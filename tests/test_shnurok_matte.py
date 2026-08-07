import importlib.util, subprocess, shutil
from pathlib import Path
import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("rembg") is None or shutil.which("ffmpeg") is None,
    reason="rembg/ffmpeg not available",
)

def _synth_clip(p: Path):
    # 1s test clip: moving white box on black (stands in for a person)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "color=c=black:s=1080x1920:d=1:r=30",
                    "-vf", "drawbox=x=440:y=700:w=200:h=500:c=white:t=fill",
                    "-c:v", "libx264", str(p)], check=True)

def test_person_matte_runs(tmp_path):
    from src.shnurok.matte import person_matte
    clip = tmp_path / "c.mp4"; _synth_clip(clip)
    out = person_matte(clip, 0.0, 1.0, tmp_path / "m.mp4")
    assert out.exists()
    # matte is a real video of the right geometry
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-of", "csv=p=0", str(out)],
                       capture_output=True, text=True)
    assert "1080,1920" in r.stdout
