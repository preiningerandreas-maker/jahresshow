"""Grafikkarte versagt mitten im Export (Praxistest: h264_qsv „Invalid FrameType:0“, obwohl
der Probe-Encode bestanden hatte). Simuliert: h264_qsv läuft hier als libx264, damit der Test
auf jedem Rechner geht, und bricht ab dem n-ten Aufruf nach einigen Bildern mit der Meldung
aus dem Praxistest ab."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from mediacheck import assert_working_format

from slideshow.core import normalize
from slideshow.core.ffmpeg import CREATIONFLAGS, FFmpegError, ffmpeg_path, probe, run_ffmpeg
from slideshow.core.importer import clip_length
from slideshow.core.project import FPS, Item, Project, Settings, TextOverlay
from slideshow.render import assemble, encoder, segment
from slideshow.render.assemble import RenderError, RenderOptions, render_project
from slideshow.render.encoder import load_prefs

pytestmark = pytest.mark.slow

QSV_ERROR = (
    "FFmpeg-Fehler (Code 3199971767):\n"
    "[h264_qsv @ 000001c8a2b3c4d0] Invalid FrameType:0.\n"
    "[vost#0:0/h264_qsv @ 000001c8a2b3c5e0] Error submitting video frame to the encoder\n"
    "Conversion failed!"
)


class FlakyGpu:
    """Ersatz für run_ffmpeg. Ab dem fail_at-ten Aufruf mit h264_qsv werden nur noch
    after_frames Bilder kodiert, dann kommt der Fehler. cpu_fails: Auch der unmittelbar
    folgende Versuch mit libx264 scheitert."""

    def __init__(self, fail_at: int, after_frames: int = 10, cpu_fails: bool = False) -> None:
        self.fail_at = fail_at
        self.after_frames = after_frames
        self.cpu_fails = cpu_fails
        self.qsv_calls = 0
        self.calls: list[tuple[str, str, bool]] = []  # (Art, Kodierer, gescheitert)
        self._just_failed = False

    def __call__(self, args, *, progress=None, cancel=None) -> None:  # noqa: ANN001
        args = [str(a) for a in args]
        enc = args[args.index("-c:v") + 1] if "-c:v" in args else ""
        kind = ("block" if "[vout]" in args else "segment" if "-filter_complex" in args
                else "video" if "-vf" in args else "other")
        if enc != "h264_qsv":
            if enc == "libx264" and self._just_failed and self.cpu_fails:
                self.calls.append((kind, enc, True))
                raise FFmpegError("FFmpeg-Fehler (Code 1):\nsimulierter Fehler mit libx264")
            self._just_failed = False
            self.calls.append((kind, enc, False))
            run_ffmpeg(args, progress=progress, cancel=cancel)
            return
        self.qsv_calls += 1
        args[args.index("-c:v") + 1] = "libx264"  # die übrigen QSV-Optionen kennt libx264 auch
        if self.qsv_calls < self.fail_at:
            self.calls.append((kind, enc, False))
            run_ffmpeg(args, progress=progress, cancel=cancel)
            return
        # Ein paar Bilder werden noch geschrieben, dann versagt die Grafikkarte
        if "-frames:v" in args:
            args[args.index("-frames:v") + 1] = str(self.after_frames)
        else:
            args[-1:-1] = ["-frames:v", str(self.after_frames)]
        run_ffmpeg(args, progress=progress, cancel=cancel)
        self.calls.append((kind, enc, True))
        self._just_failed = True
        raise FFmpegError(QSV_ERROR)

    def used(self, kind: str) -> list[str]:
        return [enc for k, enc, failed in self.calls if k == kind and not failed]


@pytest.fixture
def gpu(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """Probe-Encode meldet Quick Sync als funktionsfähig, wie im Praxistest."""
    monkeypatch.setattr(encoder, "probe_encoder", lambda name: name in ("h264_qsv", "libx264"))

    def install(fake: FlakyGpu) -> FlakyGpu:
        for module in (segment, assemble, normalize):
            monkeypatch.setattr(module, "run_ffmpeg", fake)
        return fake

    return install


def _photos(synthetic: Path, folder: Path, *extra: Item) -> Project:
    for name in ("Querformat Wörthersee.jpg", "Hochkant Größe.jpg", "Winter Schnee.heic"):
        shutil.copy2(synthetic / name, folder / name)
    return Project(
        name="Panne",
        items=[
            Item("title", duration=1.0, text=TextOverlay("Grafikkarte", position="center")),
            Item("photo", path="Querformat Wörthersee.jpg", duration=1.0),
            Item("photo", path="Hochkant Größe.jpg", duration=1.0),
            Item("photo", path="Winter Schnee.heic", duration=1.0),
            *extra,
        ],
        settings=Settings(transition_duration=0.5),
        base_dir=folder,
    )


def assert_complete(out: Path, project: Project) -> None:
    """Video vollständig: alle Bilder da, Dauer stimmt, fehlerfrei dekodierbar."""
    expected = project.total_frames(clip_length)
    v, _ = assert_working_format(out)
    assert (int(v["width"]), int(v["height"])) == (1920, 1080)
    assert int(v["nb_frames"]) == expected
    assert abs(float(probe(out)["format"]["duration"]) - expected / FPS) < 0.05
    res = subprocess.run([str(ffmpeg_path()), "-v", "error", "-i", str(out), "-f", "null", "-"],
                         capture_output=True, text=True, creationflags=CREATIONFLAGS, check=False)
    assert res.returncode == 0 and not res.stderr.strip(), res.stderr


def test_gpu_fails_in_segment_after_n_frames_video_still_complete(
        gpu, synthetic: Path, umlaut_dir: Path) -> None:  # noqa: ANN001
    project = _photos(synthetic, umlaut_dir)
    cache, out = umlaut_dir / "cache", umlaut_dir / "Show Ü.mp4"
    fake = gpu(FlakyGpu(fail_at=2, after_frames=10))  # Element 2 nach 10 Bildern

    result = render_project(project, RenderOptions(out, cache_dir=cache, max_block=2))

    assert_complete(out, project)
    assert result.encoder == "libx264" and result.fallback_element == 2
    assert result.encoder_note is not None
    assert result.encoder_note.startswith(
        "Grafikkarte hat beim Kodieren versagt, ab Element 2 mit Prozessor weitergerechnet.")
    assert "„Querformat Wörthersee.jpg“" in result.encoder_note
    # Element 2 mit dem Prozessor wiederholt, der Rest des Laufs nur noch Prozessor
    assert fake.calls[:3] == [("segment", "h264_qsv", False), ("segment", "h264_qsv", True),
                              ("segment", "libx264", False)]
    assert fake.qsv_calls == 2
    assert fake.used("block") == ["libx264", "libx264"]
    assert not list(cache.rglob("*.part.*"))
    assert "h264_qsv" in load_prefs(cache).failed

    # Nächster Export: gleich der Prozessor, mit Hinweis; Quick Sync wird nicht mehr versucht
    again = gpu(FlakyGpu(fail_at=1))
    second = render_project(project, RenderOptions(umlaut_dir / "zwei.mp4", cache_dir=cache,
                                                   max_block=2))
    assert again.qsv_calls == 0 and second.encoder == "libx264"
    assert second.fallback_element is None
    assert second.encoder_note is not None and "früheren Export" in second.encoder_note
    assert_complete(umlaut_dir / "zwei.mp4", project)


def test_gpu_fails_while_joining_all_blocks_redone_with_cpu(
        gpu, synthetic: Path, umlaut_dir: Path) -> None:  # noqa: ANN001
    project = _photos(synthetic, umlaut_dir)
    out = umlaut_dir / "Show.mp4"
    # 4 Segmente und Abschnitt 1 klappen, Abschnitt 2 (Element 3 und 4) versagt
    fake = gpu(FlakyGpu(fail_at=6, after_frames=15))

    result = render_project(project, RenderOptions(out, cache_dir=umlaut_dir / "cache",
                                                   max_block=2))

    assert_complete(out, project)
    assert result.fallback_element == 3
    assert result.encoder_note is not None and "ab Element 3 mit Prozessor" in result.encoder_note
    # Abschnitt 1 war schon mit Quick Sync fertig: Alle Abschnitte müssen vom selben Kodierer
    # stammen, also wird er mit dem Prozessor neu gerechnet
    blocks = [(enc, failed) for kind, enc, failed in fake.calls if kind == "block"]
    assert blocks == [("h264_qsv", False), ("h264_qsv", True), ("libx264", False),
                      ("libx264", False)]
    assert result.blocks_total == 2


def test_gpu_fails_while_converting_video(gpu, synthetic: Path,  # noqa: ANN001
                                          umlaut_dir: Path) -> None:
    shutil.copy2(synthetic / "Video mit Ton.mp4", umlaut_dir / "Video mit Ton.mp4")
    project = _photos(synthetic, umlaut_dir,
                      Item("video", path="Video mit Ton.mp4", start=0.5, duration=1.5))
    out = umlaut_dir / "Show.mp4"
    fake = gpu(FlakyGpu(fail_at=1, after_frames=5))  # gleich die Umwandlung des Videos

    result = render_project(project, RenderOptions(out, cache_dir=umlaut_dir / "cache"))

    assert_complete(out, project)
    assert result.fallback_element == 5 and result.videos_normalized == 1
    assert result.encoder_note is not None and "„Video mit Ton.mp4“" in result.encoder_note
    assert fake.qsv_calls == 1
    assert set(fake.used("segment")) == {"libx264"}


def test_abort_only_if_cpu_fails_too_message_names_element_and_file(
        gpu, synthetic: Path, umlaut_dir: Path) -> None:  # noqa: ANN001
    project = _photos(synthetic, umlaut_dir)
    cache = umlaut_dir / "cache"
    gpu(FlakyGpu(fail_at=3, cpu_fails=True))

    with pytest.raises(RenderError) as info:
        render_project(project, RenderOptions(umlaut_dir / "x.mp4", cache_dir=cache))
    text = str(info.value)
    assert "Element 3" in text and "„Hochkant Größe.jpg“" in text
    assert str(umlaut_dir / "Hochkant Größe.jpg") in text
    assert "libx264" in text  # die Meldung des letzten Versuchs
    assert not (umlaut_dir / "x.mp4").exists()
    # Der Prozessor hat es auch nicht geschafft: Das spricht nicht gegen die Grafikkarte
    assert load_prefs(cache).failed == {}


def test_block_error_names_elements_and_file(gpu, synthetic: Path,  # noqa: ANN001
                                             umlaut_dir: Path) -> None:
    project = _photos(synthetic, umlaut_dir)
    gpu(FlakyGpu(fail_at=6, cpu_fails=True))

    with pytest.raises(RenderError) as info:
        render_project(project, RenderOptions(umlaut_dir / "x.mp4",
                                              cache_dir=umlaut_dir / "cache", max_block=2))
    text = str(info.value)
    assert "Abschnitt 2 (Element 3 bis 4" in text and "„Hochkant Größe.jpg“" in text
