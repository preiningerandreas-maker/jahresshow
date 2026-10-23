from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import requires_ffmpeg

from slideshow.render import encoder
from slideshow.render.encoder import (
    ENCODERS,
    GPU_AUTO,
    GPU_NEVER,
    MAX_BITRATE_1080,
    EncoderChoice,
    GpuPrefs,
    bitrates,
    choose_encoder,
    detect_encoder,
    encoder_args,
    forget_failures,
    load_prefs,
    remember_failure,
    set_gpu_mode,
)


def test_order() -> None:
    assert ENCODERS == ("h264_nvenc", "h264_qsv", "h264_amf", "libx264")


@pytest.mark.parametrize("name", ENCODERS)
def test_args_high_profile_and_capped(name: str) -> None:
    args = encoder_args(name, 1080)
    assert args[args.index("-c:v") + 1] == name
    assert "high" in args
    assert args[args.index("-maxrate") + 1] == str(MAX_BITRATE_1080)
    assert args[args.index("-pix_fmt") + 1] == "yuv420p"


def test_bitrate_limit_keeps_hour_below_4gb() -> None:
    _, peak = bitrates(1080)
    assert peak <= 8_000_000
    assert 3600 * (peak + 192_000) / 8 < 4 * 1024**3


def test_unknown_encoder() -> None:
    with pytest.raises(ValueError):
        encoder_args("h265_magic", 1080)
    with pytest.raises(ValueError):
        detect_encoder(None, "h265_magic")


def test_detection_order_and_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_probe(name: str) -> bool:
        calls.append(name)
        return name in ("h264_amf", "libx264")

    monkeypatch.setattr(encoder, "probe_encoder", fake_probe)
    assert detect_encoder(tmp_path) == "h264_amf"
    assert calls == ["h264_nvenc", "h264_qsv", "h264_amf"]
    data = json.loads((tmp_path / "encoder.json").read_text("utf-8"))
    assert data["results"]["h264_amf"] is True

    calls.clear()
    assert detect_encoder(tmp_path) == "h264_amf"  # aus Cache, keine neue Probe
    assert detect_encoder(tmp_path, "libx264") == "libx264"
    assert calls == ["libx264"]
    with pytest.raises(RuntimeError, match="funktioniert hier nicht"):
        detect_encoder(tmp_path, "h264_nvenc")


@requires_ffmpeg
def test_libx264_always_works() -> None:
    assert encoder.probe_encoder("libx264")


@requires_ffmpeg
def test_real_detection_finds_something(tmp_path: Path) -> None:
    assert detect_encoder(tmp_path) in ENCODERS


def test_probe_is_realistic(tmp_path: Path) -> None:
    """Mehrere Sekunden wechselnde Inhalte, kodiert mit denselben Parametern wie im Export."""
    cmd = encoder.probe_command("h264_qsv", tmp_path)
    joined = " ".join(cmd)
    assert " ".join(encoder_args("h264_qsv", 1080, intermediate=True)) in joined
    assert " ".join(encoder_args("h264_qsv", 1080)) in joined
    assert encoder.PROBE_SECONDS >= 3
    assert cmd.count("-frames:v") == 2
    assert cmd[cmd.index("-frames:v") + 1] == str(encoder.PROBE_SECONDS * 30)
    for part in ("testsrc2", "xfade", "loop=", "noise=", "colorspace=bt709"):
        assert part in encoder.PROBE_SOURCE
    assert "null" not in cmd  # echte MP4-Dateien, wie beim Export


@requires_ffmpeg
def test_probe_writes_full_length_files(tmp_path: Path) -> None:
    import subprocess

    from slideshow.core.ffmpeg import probe

    subprocess.run(encoder.probe_command("libx264", tmp_path), check=True, capture_output=True)
    for name in ("segment.mp4", "show.mp4"):
        v = next(s for s in probe(tmp_path / name)["streams"] if s["codec_type"] == "video")
        assert int(v["nb_frames"]) == encoder.PROBE_SECONDS * 30
        assert (v["width"], v["height"], v["color_primaries"]) == (1920, 1080, "bt709")


def test_results_of_old_probe_are_not_reused(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """Der alte 1-Sekunden-Probe hatte Quick Sync durchgelassen: neu prüfen."""
    p = encoder.ffmpeg_path()
    st = p.stat()
    old_key = f"{p.resolve()}|{st.st_size}|{st.st_mtime_ns}"
    (tmp_path / "encoder.json").write_text(
        json.dumps({"ffmpeg": old_key, "results": {"h264_nvenc": False, "h264_qsv": True}}),
        "utf-8")
    calls: list[str] = []
    monkeypatch.setattr(encoder, "probe_encoder",
                        lambda name: calls.append(name) or name == "libx264")
    assert detect_encoder(tmp_path) == "libx264"
    assert calls == ["h264_nvenc", "h264_qsv", "h264_amf", "libx264"]


@pytest.fixture
def qsv_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(encoder, "probe_encoder", lambda name: name in ("h264_qsv", "libx264"))


@pytest.mark.usefixtures("qsv_works")
def test_setting_never_uses_cpu(tmp_path: Path) -> None:
    assert choose_encoder(tmp_path).name == "h264_qsv"
    set_gpu_mode(tmp_path, GPU_NEVER)
    choice = choose_encoder(tmp_path)
    assert choice == EncoderChoice("libx264")  # eigene Wahl, kein Hinweis nötig
    assert load_prefs(tmp_path).mode == GPU_NEVER
    set_gpu_mode(tmp_path, GPU_AUTO)
    assert choose_encoder(tmp_path).name == "h264_qsv"
    with pytest.raises(ValueError):
        set_gpu_mode(tmp_path, "manchmal")


@pytest.mark.usefixtures("qsv_works")
def test_remembered_failure_uses_cpu_with_note(tmp_path: Path) -> None:
    remember_failure(tmp_path, "h264_qsv")
    choice = choose_encoder(tmp_path)
    assert choice.name == "libx264"
    assert choice.note is not None and "Intel-Grafik (Quick Sync) am " in choice.note
    assert "Extras → Grafikkarte verwenden" in choice.note
    # Ausdrücklich erzwungen (Kommandozeile --encoder) gilt trotzdem
    assert choose_encoder(tmp_path, "h264_qsv").name == "h264_qsv"
    forget_failures(tmp_path)
    assert choose_encoder(tmp_path) == EncoderChoice("h264_qsv")


def test_failure_of_one_gpu_does_not_block_another(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(encoder, "probe_encoder", lambda name: True)
    remember_failure(tmp_path, "h264_qsv")
    assert choose_encoder(tmp_path) == EncoderChoice("h264_nvenc")


def test_broken_prefs_file_means_defaults(tmp_path: Path) -> None:
    (tmp_path / encoder.PREFS_FILE).write_text("{kaputt", "utf-8")
    assert load_prefs(tmp_path) == GpuPrefs()
    (tmp_path / encoder.PREFS_FILE).write_text(
        json.dumps({"mode": "egal", "failed": {"h264_qsv": "2026-10-23", "foo": "x"}}), "utf-8")
    assert load_prefs(tmp_path) == GpuPrefs(GPU_AUTO, {"h264_qsv": "2026-10-23"})
