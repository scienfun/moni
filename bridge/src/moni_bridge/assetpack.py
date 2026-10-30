"""에셋팩(moni-assets.bin) 형식. firmware/src/asset_pack.h와 같은 규격이다.

모두 리틀 엔디언.
  헤더 16바이트: "MONA", u16 버전, u8 표정 수, u8 예약, u16 폭, u16 높이, u32 CRC32
  표정 항목 16바이트: name[10], u8 프레임 수, u8 fps, u32 첫 프레임 번호
  프레임 항목 8바이트: u32 파일 오프셋, u32 길이
  JPEG 데이터
CRC32는 헤더 뒤의 모든 바이트를 대상으로 한다.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

MAGIC = b"MONA"
VERSION = 1
HEADER = struct.Struct("<4sHBBHHI")
EMOTION = struct.Struct("<10sBBI")
FRAME = struct.Struct("<II")


@dataclass
class EmotionFrames:
    name: str
    fps: int
    frames: list[bytes]


def build_pack(emotions: list[EmotionFrames], width: int, height: int) -> bytes:
    frame_count = sum(len(e.frames) for e in emotions)
    data_start = HEADER.size + EMOTION.size * len(emotions) + FRAME.size * frame_count

    emotion_table = bytearray()
    frame_table = bytearray()
    data = bytearray()
    index = 0
    for emotion in emotions:
        name = emotion.name.encode("ascii")
        if len(name) > 9:
            raise ValueError(f"표정 이름이 깁니다: {emotion.name}")
        emotion_table += EMOTION.pack(name, len(emotion.frames), emotion.fps, index)
        for frame in emotion.frames:
            frame_table += FRAME.pack(data_start + len(data), len(frame))
            data += frame
        index += len(emotion.frames)

    body = bytes(emotion_table + frame_table + data)
    header = HEADER.pack(
        MAGIC, VERSION, len(emotions), 0, width, height, zlib.crc32(body)
    )
    return header + body


def read_pack(blob: bytes) -> dict[str, list[bytes]]:
    magic, version, count, _, _, _, crc = HEADER.unpack_from(blob)
    body = blob[HEADER.size :]
    if magic != MAGIC or version != VERSION or zlib.crc32(body) != crc:
        raise ValueError("에셋팩이 손상되었습니다")

    frames_at = HEADER.size + EMOTION.size * count
    result: dict[str, list[bytes]] = {}
    for i in range(count):
        raw, frame_count, _, first = EMOTION.unpack_from(
            blob, HEADER.size + EMOTION.size * i
        )
        items = []
        for n in range(first, first + frame_count):
            offset, length = FRAME.unpack_from(blob, frames_at + FRAME.size * n)
            items.append(blob[offset : offset + length])
        result[raw.rstrip(b"\0").decode("ascii")] = items
    return result
