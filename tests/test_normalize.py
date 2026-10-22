from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

import pytest
from mediacheck import (
    first_bright_frame_time,
    first_loud_sample_time,
    mean_luma,
    streams,
    video_pts,
)

from slideshow.core import normalize
from slideshow.core.ffmpeg import Cancelled, FFmpegError
from slideshow.core.importer import video_info
from slideshow.core.normalize import (
    BackgroundNormalizer,
    NormalizeProgress,
    cached_normalized,
    normalize_video,
)
from slideshow.core.project import Item, Project
from slideshow.render.segment import RenderConfig, segment_key

pytestmark = pytest.mark.slow


def _copy(synthetic: Path, name: str, target_dir: Path, new_name: str | None = None) -> Path:
    dst = target_dir / (new_name or name)
    shutil.copy2(synthetic / name, dst)
    return dst


def _assert_working_format(path: Path) -> tuple[dict, dict | None]:
    v, a = streams(path)
    assert v["codec_name"] == "h264"
    assert v["pix_fmt"] == "yuv420p"
    assert v["r_frame_rate"] == "30/1"
    assert v["avg_frame_rate"] == "30/1"
    assert int(v["height"]) <= 1080
    assert v.get("color_transfer") == "bt709"
    assert v.get("color_primaries") == "bt709"
    assert not any("rotation" in sd for sd in v.get("side_data_list", []))
    if a is not None:
        assert a["codec_name"] == "aac"
        assert a["sample_rate"] == "48000"
    return v, a


# --- (4) VFR → 30 fps CFR, Ton synchron -------------------------------------------------


def test_vfr_becomes_cfr_30fps(synthetic: Path, umlaut_dir: Path) -> None:
    src = _copy(synthetic, "VFR Handy.mp4", umlaut_dir, "Handy Größe – VFR.mp4")
    sv, _ = streams(src)
    assert sv["r_frame_rate"] != sv["avg_frame_rate"]  # Quelle ist wirklich VFR

    out = normalize_video(src, umlaut_dir / "Cache Ü")
    assert "Cache Ü" in str(out)
    v, a = _assert_working_format(out)
    assert a is not None

    # Konstante Bildabstände von 1/30 s
    pts = video_pts(out)
    gaps = [b - a for a, b in zip(pts, pts[1:], strict=False)]
    assert gaps and all(abs(g - 1 / 30) < 0.002 for g in gaps)
    assert len(pts) == pytest.approx(5 * 30, abs=2)

    # Ton-Bild-Versatz laut ffprobe unter 50 ms (Start und Länge)
    assert abs(float(v["start_time"]) - float(a["start_time"])) < 0.05
    assert abs(float(v["duration"]) - float(a["duration"])) < 0.05


def test_vfr_flash_and_beep_stay_in_sync(synthetic: Path, umlaut_dir: Path) -> None:
    """Blitz und Piepton liegen in der Quelle beide bei 2,0 s und danach höchstens 50 ms
    auseinander."""
    src = _copy(synthetic, "VFR Blitz Ton.mp4", umlaut_dir)
    out = normalize_video(src, umlaut_dir / "cache")
    v, a = _assert_working_format(out)
    assert a is not None
    flash = first_bright_frame_time(out)
    beep = first_loud_sample_time(out)
    assert flash == pytest.approx(2.0, abs=0.05)
    assert beep == pytest.approx(2.0, abs=0.05)
    assert abs(flash - beep) < 0.05
    assert abs(float(v["start_time"]) - float(a["start_time"])) < 0.05


# --- (5) HDR → bt709 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "transfer", "size"),
    [
        ("HDR Hochkant PQ.mp4", "smpte2084", (608, 1080)),  # gedreht, auf Höhe 1080
        ("HDR HLG.mp4", "arib-std-b67", (1280, 720)),
    ],
)
def test_hdr_is_tonemapped_to_bt709(synthetic: Path, umlaut_dir: Path, name: str,
                                    transfer: str, size: tuple[int, int]) -> None:
    src = _copy(synthetic, name, umlaut_dir, f"Größe {name}")
    info = video_info(src)
    assert info.hdr and info.color_transfer == transfer

    out = normalize_video(src, umlaut_dir / "cache")
    v, a = _assert_working_format(out)
    assert v["color_transfer"] not in ("smpte2084", "arib-std-b67")
    assert v.get("color_space") == "bt709"
    assert (int(v["width"]), int(v["height"])) == size
    assert a is not None
    assert not video_info(out).hdr
    # Kein ausgewaschenes oder schwarzes Bild
    assert 30 < mean_luma(out) < 220


def test_portrait_video_capped_at_1080(synthetic: Path, umlaut_dir: Path) -> None:
    src = _copy(synthetic, "Hochkant Video.mp4", umlaut_dir)  # 1080×1920
    v, _ = _assert_working_format(normalize_video(src, umlaut_dir / "cache"))
    assert (int(v["width"]), int(v["height"])) == (608, 1080)


# --- Cache, Fortschritt, Fallback ---------------------------------------------------------


def test_cache_and_progress(synthetic: Path, umlaut_dir: Path) -> None:
    src = _copy(synthetic, "Video mit Ton.mp4", umlaut_dir, "Ölfeld.mp4")
    cache = umlaut_dir / "cache"
    assert cached_normalized(src, cache) is None
    seen: list[NormalizeProgress] = []
    out = normalize_video(src, cache, progress=seen.append)
    assert cached_normalized(src, cache) == out
    assert seen[-1].fraction == 1.0
    assert all(0 <= p.fraction <= 1 for p in seen)
    assert [p.fraction for p in seen] == sorted(p.fraction for p in seen)
    # keine halben Dateien im Cache
    assert sorted(p.name for p in out.parent.iterdir()) == [out.name]

    mtime = out.stat().st_mtime_ns
    assert normalize_video(src, cache, progress=seen.append) == out
    assert out.stat().st_mtime_ns == mtime  # aus dem Cache, nicht neu

    # Geänderte Quelldatei → neu umwandeln
    st = src.stat()
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert cached_normalized(src, cache) is None


def test_progress_eta() -> None:
    p = NormalizeProgress(done=25, total=100, elapsed=10)
    assert p.fraction == 0.25
    assert p.eta == pytest.approx(30)
    assert NormalizeProgress(0, 100, 1).eta is None
    assert NormalizeProgress(5, 0, 1).fraction == 0.0


def test_falls_back_to_cpu_when_hw_decoding_fails(
    synthetic: Path, umlaut_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T450s: HEVC 10 Bit kann nicht in Hardware dekodiert werden → CPU."""
    calls: list[list[str]] = []
    real = normalize.run_ffmpeg

    def fake(args, **kw):  # type: ignore[no-untyped-def]
        calls.append(list(map(str, args)))
        if "-hwaccel" in args:
            raise FFmpegError("Hardware-Dekodierung nicht möglich")
        return real(args, **kw)

    monkeypatch.setattr(normalize, "run_ffmpeg", fake)
    src = _copy(synthetic, "HDR HLG.mp4", umlaut_dir)
    out = normalize_video(src, umlaut_dir / "cache")
    _assert_working_format(out)
    assert len(calls) == 2
    assert "-hwaccel" in calls[0] and "-hwaccel" not in calls[1]


def test_cancel_leaves_no_partial_file(synthetic: Path, umlaut_dir: Path) -> None:
    src = _copy(synthetic, "Video mit Ton.mp4", umlaut_dir)
    cache = umlaut_dir / "cache"
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        normalize_video(src, cache, cancel=cancel)
    assert cached_normalized(src, cache) is None
    assert list((cache / "normalized").iterdir()) == []


def test_background_normalizer(synthetic: Path, umlaut_dir: Path) -> None:
    a = _copy(synthetic, "VFR Handy.mp4", umlaut_dir, "Ä.mp4")
    b = _copy(synthetic, "HDR HLG.mp4", umlaut_dir, "Ö.mp4")
    broken = umlaut_dir / "kaputt.mp4"
    broken.write_bytes(b"\x00" * 64)
    done: dict[Path, Path] = {}
    errors: list[Path] = []
    progress: set[Path] = set()
    worker = BackgroundNormalizer(
        umlaut_dir / "cache",
        on_progress=lambda s, _p: progress.add(s),
        on_done=lambda s, o: done.__setitem__(s, o),
        on_error=lambda s, _e: errors.append(s),
    )
    worker.submit([a, broken, b])
    worker.wait()
    worker.close()
    assert set(done) == {a, b}
    assert errors == [broken]
    assert progress >= {a, b}
    for out in done.values():
        _assert_working_format(out)


def test_background_cancel_does_not_hang(synthetic: Path, umlaut_dir: Path) -> None:
    srcs = [_copy(synthetic, "Video mit Ton.mp4", umlaut_dir, f"{i}.mp4") for i in range(3)]
    worker = BackgroundNormalizer(umlaut_dir / "cache")
    worker.submit(srcs)
    worker.cancel()
    worker.wait()
    worker.close()


def test_render_uses_normalized_video(synthetic: Path, umlaut_dir: Path) -> None:
    src = _copy(synthetic, "VFR Handy.mp4", umlaut_dir)
    cache = umlaut_dir / "cache"
    project = Project(items=[Item("video", path=str(src))], base_dir=umlaut_dir)
    cfg = RenderConfig(1920, 1080, "libx264", cache)
    entry = project.timeline(lambda p: video_info(p).duration)[0]
    before = segment_key(project, entry, cfg)
    normalize_video(src, cache)
    assert segment_key(project, entry, cfg) != before
