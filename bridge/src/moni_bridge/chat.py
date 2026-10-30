"""웹 채팅 대화 내역. 한 줄에 JSON 하나씩 파일에 쌓는다."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

MAX_KEPT = 500


class ChatLog:
    def __init__(self, path: Path, limit: int = MAX_KEPT) -> None:
        self.path = path
        self.limit = limit
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        entries = []
        for line in lines:
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue  # 쓰는 도중 끊긴 줄은 건너뛴다.
        return entries

    def history(self) -> list[dict]:
        with self._lock:
            return self._read()

    def add(self, role: str, text: str, **extra) -> dict:
        entry = {"role": role, "text": text, "ts": int(time.time()), **extra}
        with self._lock:
            entries = self._read()[-(self.limit - 1):] + [entry]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries),
                encoding="utf-8",
            )
        return entry

    def clear(self) -> None:
        with self._lock:
            self.path.unlink(missing_ok=True)
