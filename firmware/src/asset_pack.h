// 에셋팩 규격. bridge/src/moni_bridge/assetpack.py와 같아야 한다.
// Arduino에 의존하지 않아 PC에서도 시험할 수 있다(firmware/test/native).
#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace moni {

const size_t kPackHeaderSize = 16;
const size_t kPackEmotionSize = 16;
const size_t kPackFrameSize = 8;
const uint8_t kMaxEmotions = 8;
const uint32_t kMaxFrameBytes = 8 * 1024;

struct PackHeader {
  uint8_t emotionCount;
  uint16_t width;
  uint16_t height;
  uint32_t crc;
};

struct PackEmotion {
  char name[11];
  uint8_t frames;
  uint8_t fps;
  uint32_t firstFrame;
};

struct PackFrame {
  uint32_t offset;
  uint32_t length;
};

inline uint16_t readU16(const uint8_t* p) {
  return uint16_t(p[0]) | uint16_t(p[1]) << 8;
}

inline uint32_t readU32(const uint8_t* p) {
  return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 |
         uint32_t(p[3]) << 24;
}

inline bool parseHeader(const uint8_t* raw, PackHeader& out) {
  if (memcmp(raw, "MONA", 4) != 0 || readU16(raw + 4) != 1) return false;
  out.emotionCount = raw[6];
  out.width = readU16(raw + 8);
  out.height = readU16(raw + 10);
  out.crc = readU32(raw + 12);
  return out.emotionCount > 0 && out.emotionCount <= kMaxEmotions;
}

inline void parseEmotion(const uint8_t* raw, PackEmotion& out) {
  memcpy(out.name, raw, 10);
  out.name[10] = '\0';
  out.frames = raw[10];
  out.fps = raw[11];
  out.firstFrame = readU32(raw + 12);
}

inline void parseFrame(const uint8_t* raw, PackFrame& out) {
  out.offset = readU32(raw);
  out.length = readU32(raw + 4);
}

// zlib 호환 CRC32. 표 없이 계산해 RAM을 쓰지 않는다.
inline uint32_t crc32Update(uint32_t crc, const uint8_t* data, size_t length) {
  crc = ~crc;
  while (length--) {
    crc ^= *data++;
    for (int bit = 0; bit < 8; bit++) {
      crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
  }
  return ~crc;
}

}  // namespace moni
