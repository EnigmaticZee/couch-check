"""Experiment "iscci": the ISCCI rubric, scored and shown alongside (or instead of) rubric v5.1.

Switched with PG_EXPERIMENTAL: "off" (default), "both" (the main score and ISCCI side by side) or "only" (ISCCI alone).
"""
from __future__ import annotations

from typing import Optional

from .service import IscciService

NAME = "iscci"
_service: Optional[IscciService] = None


def init(production_service) -> None:
    global _service
    _service = IscciService(production_service)


def get() -> IscciService:
    return _service
