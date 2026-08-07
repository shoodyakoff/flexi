from pathlib import Path
import shutil, subprocess, pytest

SRC = Path("assets/shnurok_test")
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or not SRC.exists(),
                                reason="ffmpeg or assets/shnurok_test missing")


def test_build_produces_final(tmp_path):
    from src.shnurok.build import build_shnurok
    work = tmp_path / "job"; shutil.copytree(SRC, work)
    outs = build_shnurok(
        work, styles=("classic",),
        talking_head=work / "IMG_3968.MOV",
        voice=work / "Numeris.m4a",
        graphic=work / "IMG_9585.JPG",
        broll=[work / f"IMG_{n}.MP4" for n in (9575, 9576, 9577, 9578, 9580, 9581, 9582)],
    )
    f = outs["classic"]
    assert f.exists()
    d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", str(f)], capture_output=True, text=True).stdout)
    assert d > 30
