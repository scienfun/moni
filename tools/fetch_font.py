"""OFL 한글 폰트 Jua를 assets/fonts에 내려받는다."""

from __future__ import annotations

import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/google/fonts/main/ofl/jua"
TARGET = Path(__file__).resolve().parents[1] / "assets" / "fonts"


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    for name in ("Jua-Regular.ttf", "OFL.txt"):
        path = TARGET / name
        if path.exists():
            continue
        with urllib.request.urlopen(f"{BASE}/{name}", timeout=30) as response:
            path.write_bytes(response.read())
        print(f"받음: {path.name}")


if __name__ == "__main__":
    main()
