"""Helpers for keeping `position` contiguous (0..n-1) within a list of ordered rows."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class Positioned(Protocol):
    id: int
    position: int


def place[T: Positioned](items: Sequence[T], item: T, index: int) -> list[T]:
    """Return `items` without `item`, then with `item` inserted at `index` (clamped)."""
    rest = [i for i in items if i.id != item.id]
    index = max(0, min(index, len(rest)))
    rest.insert(index, item)
    return rest


def renumber[T: Positioned](items: Sequence[T]) -> None:
    for position, item in enumerate(items):
        if item.position != position:
            item.position = position
