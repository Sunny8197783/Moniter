"""앱 설정(스위치 상태 등)을 JSON 파일에 저장한다. 히스토리와 같은 폴더를 쓴다."""
from __future__ import annotations

import json
import os
from pathlib import Path

from history import default_history_path


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else default_history_path().with_name("settings.json")
        self._data: dict = self._load()

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass  # 설정 저장 실패는 앱 동작에 영향이 없으므로 무시한다

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}
