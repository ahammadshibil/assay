"""Vertical plug-in layer.

The core engine stays a single, domain-agnostic provenance agent. A vertical
module attaches *extra* sources behind it (e.g. clinical trials for biotech,
orbital data for space) and returns additional flags + raw findings. Verticals
never replace the core — they augment it.

To add a vertical: subclass VerticalModule, set `name`, implement `evaluate`,
decorate with @register, and import it from this package's __init__ so it lands
in the REGISTRY.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from ..models import Flag


@dataclass
class VerticalContext:
    founder: str
    company: str
    institution: str | None = None


@dataclass
class VerticalResult:
    flags: list[Flag] = field(default_factory=list)
    findings: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class VerticalModule(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def evaluate(self, ctx: VerticalContext) -> VerticalResult:
        """Run vertical-specific sources and return extra flags + findings."""


REGISTRY: dict[str, type[VerticalModule]] = {}


def register(cls: type[VerticalModule]) -> type[VerticalModule]:
    REGISTRY[cls.name] = cls
    return cls


def get_vertical(name: str | None) -> VerticalModule | None:
    if not name:
        return None
    cls = REGISTRY.get(name.lower())
    return cls() if cls else None


# Import concrete modules so they self-register. Add new verticals here.
from . import biotech  # noqa: E402,F401
