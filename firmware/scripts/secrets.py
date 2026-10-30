"""bridge/.env의 토큰을 펌웨어 빌드 매크로로 넣는다. 값은 저장소에 남지 않는다."""

import os
from pathlib import Path

Import("env")

REQUIRED = ("MONI_DEVICE_TOKEN", "MONI_OTA_PASSWORD")

values = {}
env_file = Path(env.subst("$PROJECT_DIR")).parent / "bridge" / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")

for name in REQUIRED:
    value = os.environ.get(name) or values.get(name)
    if not value:
        raise SystemExit(f"{name}가 없습니다. `make setup`을 먼저 실행하세요.")
    env.Append(CPPDEFINES=[(name, env.StringifyMacro(value))])
