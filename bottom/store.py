"""Saves raw responses under data/raw/<date>/ so any day can be recomputed."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd


class RawStore:
    def __init__(self, root: Path, day: date, enabled: bool = True) -> None:
        self.dir = Path(root) / day.isoformat()
        self.enabled = enabled

    def _path(self, name: str) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        return self.dir / name

    def save_json(self, name: str, payload: Any) -> None:
        if self.enabled:
            self._path(name).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def save_bytes(self, name: str, content: bytes) -> None:
        if self.enabled:
            self._path(name).write_bytes(content)

    def save_frame(self, name: str, frame: pd.DataFrame | pd.Series) -> None:
        if self.enabled:
            frame.to_csv(self._path(name))


class NullStore(RawStore):
    """A store that saves nothing, for tests and dry runs."""

    def __init__(self) -> None:
        super().__init__(Path("."), date.today(), enabled=False)
