"""RP2040(MicroPython) 보드와 USB 시리얼로 파일을 주고받는다. 표준 라이브러리만 쓴다(macOS·Linux).

  python tools/rp2040_repl.py PORT ls
  python tools/rp2040_repl.py PORT get 보드파일 [로컬파일]
  python tools/rp2040_repl.py PORT put 로컬파일 보드파일
  python tools/rp2040_repl.py PORT reset
  python tools/rp2040_repl.py PORT install   모니 펌웨어를 main.py로 올린다(기존 main.py는 main_prev.py로 보관)
"""

from __future__ import annotations

import os
import select
import sys
import termios
import time


class Board:
    def __init__(self, port: str) -> None:
        self.buf = b""
        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(self.fd)
        attrs[0] = attrs[1] = attrs[3] = 0
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)

    def close(self) -> None:
        os.close(self.fd)

    def _read_until(self, marker: bytes, timeout: float) -> bytes:
        """marker까지 읽어 돌려주고, 그 뒤에 온 바이트는 다음 읽기를 위해 남긴다."""
        end = time.time() + timeout
        while marker not in self.buf and time.time() < end:
            if select.select([self.fd], [], [], 0.1)[0]:
                try:
                    self.buf += os.read(self.fd, 4096)
                except BlockingIOError:
                    pass
        head, found, tail = self.buf.partition(marker)
        if not found:
            data, self.buf = self.buf, b""
            return data
        self.buf = tail
        return head + found

    def _drain(self, quiet: float = 0.3) -> bytes:
        data, self.buf = self.buf, b""
        while select.select([self.fd], [], [], quiet)[0]:
            try:
                data += os.read(self.fd, 4096)
            except BlockingIOError:
                break
        return data

    def interrupt(self) -> None:
        os.write(self.fd, b"\x03\x03")
        self._drain()

    def exec(self, code: str, timeout: float = 20) -> str:
        """raw REPL에서 코드를 실행하고 표준 출력을 돌려준다."""
        self.interrupt()
        os.write(self.fd, b"\x01")  # raw REPL
        self._read_until(b"raw REPL; CTRL-B to exit\r\n>", 2)
        payload = code.encode()
        for i in range(0, len(payload), 256):
            os.write(self.fd, payload[i : i + 256])
            time.sleep(0.01)
        os.write(self.fd, b"\x04")
        self._read_until(b"OK", 2)
        out = self._read_until(b"\x04", timeout)
        err = self._read_until(b"\x04", 2)
        os.write(self.fd, b"\x02")  # 일반 REPL로 복귀
        self._drain()
        if err.rstrip(b"\x04").strip():
            raise RuntimeError(err.rstrip(b"\x04").decode("utf-8", "replace"))
        return out.rstrip(b"\x04").decode("utf-8", "replace")

    def get(self, name: str) -> bytes:
        hexed = self.exec(
            f"import binascii\nf=open({name!r},'rb')\n"
            "while True:\n b=f.read(512)\n if not b:break\n print(binascii.hexlify(b).decode())\nf.close()",
            timeout=60,
        )
        return bytes.fromhex("".join(hexed.split()))

    def put(self, name: str, blob: bytes) -> None:
        self.exec(f"f=open({name!r},'wb')")
        for i in range(0, len(blob), 256):
            self.exec(f"f.write(bytes.fromhex('{blob[i:i + 256].hex()}'))")
        self.exec("f.close()")

    def reset(self) -> None:
        self.interrupt()
        os.write(self.fd, b"\x04")
        self._drain(1.0)


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    board = Board(argv[1])
    try:
        command = argv[2]
        if command == "ls":
            print(board.exec("import os\nfor n in os.listdir():print(n, os.stat(n)[6])"))
        elif command == "get":
            blob = board.get(argv[3])
            target = argv[4] if len(argv) > 4 else os.path.basename(argv[3])
            open(target, "wb").write(blob)
            print(f"{argv[3]} -> {target} ({len(blob)} bytes)")
        elif command == "put":
            blob = open(argv[3], "rb").read()
            board.put(argv[4], blob)
            print(f"{argv[3]} -> {argv[4]} ({len(blob)} bytes)")
        elif command == "install":
            firmware = os.path.join(os.path.dirname(__file__), "..", "firmware-rp2040", "main.py")
            blob = open(firmware, "rb").read()
            kept = board.exec(
                "import os\n"
                "if 'main.py' in os.listdir() and 'main_prev.py' not in os.listdir():\n"
                " os.rename('main.py','main_prev.py');print('kept')"
            ).strip()
            if "ssd1306.py" not in board.exec("import os\nprint(os.listdir())"):
                print("보드에 ssd1306.py가 없습니다. MicroPython SSD1306 드라이버를 먼저 올려 주세요.")
                return 1
            board.put("main.py", blob)
            board.reset()
            print(f"main.py 설치 완료 ({len(blob)} bytes)" + (", 기존 main.py는 main_prev.py로 보관" if kept else ""))
        elif command == "reset":
            board.reset()
        else:
            print(__doc__)
            return 2
    finally:
        board.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
