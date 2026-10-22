"""Planung der Blöcke ohne FFmpeg."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from slideshow.core.project import Item, Project, Settings
from slideshow.render.assemble import (
    RenderError,
    block_graph,
    estimate_size,
    plan_blocks,
    select_section,
)
from slideshow.render.audio import duck_expression


def _project(n: int, seed: int = 1, chapters: bool = False) -> Project:
    rnd = random.Random(seed)
    items = []
    for i in range(n):
        items.append(Item(
            "photo",
            path=f"{i}.jpg",
            duration=rnd.choice([0.5, 1.0, 2.0, 3.0, 4.0, 6.0]),
            transition=rnd.choice(["fade", "fadeblack", "wipeleft", "slideleft", "none"]),
            transition_duration=rnd.choice([0.3, 1.0, 1.5]),
            chapter=f"2026-{i // 7 + 1:02d}" if chapters else None,
        ))
    return Project(items=items, settings=Settings(), base_dir=Path("."))


@pytest.mark.parametrize("n,max_block,chapters", [
    (1, 20, False), (5, 20, False), (45, 20, False), (45, 3, True), (30, 2, False), (61, 20, True),
])
def test_blocks_add_up_to_total(n: int, max_block: int, chapters: bool) -> None:
    p = _project(n, seed=n, chapters=chapters)
    tl = p.timeline(lambda _: 1.0)
    total = p.total_frames(lambda _: 1.0)
    blocks = plan_blocks(tl, max_block)
    assert sum(b.frames for b in blocks) == total
    for b in blocks:
        main = [part for part in b.parts if part.head is None]
        assert len(main) <= max_block
        assert all(part.frames > 0 for part in b.parts)
        if chapters:
            assert len({part.entry.item.chapter for part in main}) == 1
    # jedes Element kommt genau einmal als Hauptteil vor
    mains = [part.entry.index for b in blocks for part in b.parts if part.head is None]
    assert mains == list(range(n))


def test_lookahead_carries_transition_across_blocks() -> None:
    p = Project(items=[Item("photo", path=f"{i}.jpg", duration=4.0) for i in range(5)],
                base_dir=Path("."))
    blocks = plan_blocks(p.timeline(lambda _: 1.0), max_block=2)
    assert len(blocks) == 3
    first = blocks[0]
    assert first.parts[-1].head == 30  # Anfang von Element 3 als Vorgriff
    assert blocks[1].parts[0].skip == 30  # Element 3 beginnt im nächsten Block später
    assert blocks[2].parts[-1].head is None  # letzter Block ohne Vorgriff
    g = block_graph(first)
    assert "xfade=transition=fade:duration=1.000000:offset=3.000000" in g
    assert "acrossfade" in g and "trim=end_frame=30" in g


def test_hard_cut_uses_concat() -> None:
    p = Project(items=[Item("photo", path="a.jpg"), Item("photo", path="b.jpg",
                                                         transition="none")],
                base_dir=Path("."))
    g = block_graph(plan_blocks(p.timeline(lambda _: 1.0))[0])
    assert "concat=n=2:v=1:a=1" in g and "xfade" not in g


def test_select_section() -> None:
    p = _project(10)
    assert len(select_section(p, 3, 5).items) == 3
    assert select_section(p, None, None) is p
    with pytest.raises(RenderError):
        select_section(p, 8, 2)


def test_estimate_size_warns_for_long_show() -> None:
    assert estimate_size(3600, 1080) < 4 * 1024**3
    assert estimate_size(5 * 3600, 1080) > 4 * 1024**3


def test_duck_expression() -> None:
    assert duck_expression([], 0.25) == "1"
    expr = duck_expression([(2.0, 5.0), (9.0, 10.0)], 0.25, ramp=0.5)
    assert expr.startswith("1-0.750*clip(")
    assert "(t-1.500)/0.5" in expr and "(5.500-t)/0.5" in expr
