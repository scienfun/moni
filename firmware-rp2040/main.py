# 모니 OLED 펌웨어: Waveshare RP2040-Zero + SSD1306 128x64 (I2C, SDA=GP0, SCL=GP1).
# USB 시리얼로 한 줄씩 명령을 받고, 처리하면 "OK ..." 또는 "ERR ..."를 돌려준다.
#   PING                    -> OK moni 1
#   IMG <base64>            1,024바이트 프레임 한 장을 표시한다.
#   ANIM <지연ms> <base64>  프레임 여러 장(1,024바이트씩)을 반복 재생한다.
#   BAND <base64>           위 16줄(노랑 띠) 256바이트. 프레임이 바뀌어도 유지한다.
#   CONTRAST <0-255>        밝기
#   INVERT <0|1>            흑백 반전
#   STATUS                  프레임 수와 남은 메모리
import binascii
import gc
import sys
import time

from machine import I2C, Pin
from ssd1306 import SSD1306_I2C

try:
    import select
except ImportError:
    import uselect as select

WIDTH = 128
HEIGHT = 64
FRAME_BYTES = WIDTH * HEIGHT // 8
MAX_FRAMES = 12
VERSION = 2
BAND_BYTES = WIDTH * 16 // 8


def draw_boot(display):
    display.fill(0)
    display.text("moni", 48, 16, 1)
    display.text("waiting USB", 20, 36, 1)
    display.show()


def show(display, frame, band):
    display.buffer[:] = frame
    display.buffer[:BAND_BYTES] = band
    display.show()


def decode_frames(payload):
    blob = binascii.a2b_base64(payload)
    if not blob or len(blob) % FRAME_BYTES:
        raise ValueError("bad length {}".format(len(blob)))
    count = len(blob) // FRAME_BYTES
    if count > MAX_FRAMES:
        raise ValueError("too many frames {}".format(count))
    view = memoryview(blob)
    return [view[i * FRAME_BYTES:(i + 1) * FRAME_BYTES] for i in range(count)]


def handle(line, state, display):
    parts = line.split(None, 2)
    if not parts:
        return None
    name = parts[0].upper()
    if name == "PING":
        return "OK moni {}".format(VERSION)
    if name == "IMG" and len(parts) == 2:
        state["frames"] = decode_frames(parts[1])[:1]
        state["index"] = 0
        show(display, state["frames"][0], state["band"])
        return "OK img"
    if name == "ANIM" and len(parts) == 3:
        delay = max(30, min(5000, int(parts[1])))
        frames = decode_frames(parts[2])
        state["frames"] = frames
        state["delay"] = delay
        state["index"] = 0
        state["last"] = time.ticks_ms()
        show(display, frames[0], state["band"])
        return "OK anim {}".format(len(frames))
    if name == "BAND" and len(parts) == 2:
        band = binascii.a2b_base64(parts[1])
        if len(band) != BAND_BYTES:
            raise ValueError("bad band {}".format(len(band)))
        state["band"] = band
        if state["frames"]:
            show(display, state["frames"][state["index"]], band)
        return "OK band"
    if name == "CONTRAST" and len(parts) == 2:
        display.contrast(max(0, min(255, int(parts[1]))))
        return "OK contrast"
    if name == "INVERT" and len(parts) == 2:
        display.invert(1 if int(parts[1]) else 0)
        return "OK invert"
    if name == "STATUS":
        gc.collect()
        return "OK frames={} delay={} mem={}".format(
            len(state["frames"]), state["delay"], gc.mem_free()
        )
    return "ERR unknown"


def main():
    i2c = I2C(0, sda=Pin(0), scl=Pin(1), freq=400000)
    display = SSD1306_I2C(WIDTH, HEIGHT, i2c, addr=0x3C)
    draw_boot(display)

    poll = select.poll()
    poll.register(sys.stdin, select.POLLIN)
    state = {
        "frames": [], "delay": 200, "index": 0, "last": time.ticks_ms(),
        "band": bytes(BAND_BYTES),
    }
    print("READY moni {}".format(VERSION))

    while True:
        if poll.poll(0):
            line = sys.stdin.readline()
            try:
                reply = handle(line.strip(), state, display)
            except Exception as exc:
                reply = "ERR {}".format(exc)
            if reply:
                print(reply)
            gc.collect()
        frames = state["frames"]
        if len(frames) > 1 and time.ticks_diff(time.ticks_ms(), state["last"]) >= state["delay"]:
            state["last"] = time.ticks_ms()
            state["index"] = (state["index"] + 1) % len(frames)
            show(display, frames[state["index"]], state["band"])
        time.sleep_ms(5)


try:
    main()
except Exception as exc:
    try:
        i2c = I2C(0, sda=Pin(0), scl=Pin(1), freq=400000)
        display = SSD1306_I2C(WIDTH, HEIGHT, i2c, addr=0x3C)
        display.fill(0)
        display.text("BOOT ERROR", 0, 0, 1)
        display.text(str(exc)[:16], 0, 16, 1)
        display.show()
    except Exception:
        pass
    while True:
        time.sleep(1)
