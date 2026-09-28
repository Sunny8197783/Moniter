"""최근 결과(오답노트)를 JSON 파일에 저장한다."""
from __future__ import annotations

import json
import os
from pathlib import Path

from gemini_client import QuizResult

MAX_ITEMS = 10


def default_history_path() -> Path:
    """Windows: %APPDATA%\\QuizStudyHelper\\history.json, 그 외: ~/.quiz_study_helper/history.json"""
    appdata = os.getenv("APPDATA")
    base = Path(appdata) / "QuizStudyHelper" if appdata else Path.home() / ".quiz_study_helper"
    return base / "history.json"


class History:
    def __init__(self, path: Path | None = None, max_items: int = MAX_ITEMS):
        self.path = Path(path) if path else default_history_path()
        self.max_items = max_items
        self._items: list[QuizResult] = self._load()

    @property
    def items(self) -> list[QuizResult]:
        """최신순 목록 (복사본)."""
        return list(self._items)

    def add(self, result: QuizResult) -> None:
        self._items.insert(0, result)
        del self._items[self.max_items :]
        self._save()

    def clear(self) -> None:
        self._items = []
        self._save()

    def _load(self) -> list[QuizResult]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError):
            return []  # 손상된 파일은 무시하고 새로 시작
        if not isinstance(data, list):
            return []
        return [QuizResult.from_dict(d) for d in data if isinstance(d, dict)][: self.max_items]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps([r.to_dict() for r in self._items], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)  # 저장 도중 종료돼도 기존 파일이 깨지지 않도록
