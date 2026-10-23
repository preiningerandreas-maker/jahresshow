"""Aufrufe der Kommandozeile, wie sie beim fertigen Paket vorkommen."""

from __future__ import annotations

from slideshow.cli import normalize_args


def test_no_arguments_start_gui() -> None:
    assert normalize_args([]) == ["gui"]


def test_render_short_form() -> None:
    assert normalize_args(["--render", "tests/demo.json", "-o", "out/pkg.mp4"]) == \
        ["render", "tests/demo.json", "-o", "out/pkg.mp4"]


def test_files_without_command_open_gui() -> None:
    """Projekt oder Ordner im Explorer auf Flashback.exe gezogen."""
    assert normalize_args([r"C:\Fotos\Urlaub Wörthersee 2026"]) == \
        ["gui", r"C:\Fotos\Urlaub Wörthersee 2026"]
    assert normalize_args(["Projekt.json", "a.jpg"]) == ["gui", "Projekt.json", "a.jpg"]


def test_commands_and_options_unchanged() -> None:
    for args in (["gui", "a.jpg"], ["render", "p.json"], ["encoders"], ["--help"]):
        assert normalize_args(args) == args
