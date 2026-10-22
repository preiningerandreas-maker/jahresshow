from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import requires_ffmpeg

from slideshow.render import encoder
from slideshow.render.encoder import (
    ENCODERS,
    MAX_BITRATE_1080,
    bitrates,
    detect_encoder,
    encoder_args,
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
