// Arduino 없이 PC에서 돌리는 시험: clang++ -std=c++17 -I../../src test_pure.cpp
// 사용법: a.out <python이 만든 pack 파일>
#include <assert.h>
#include <stdio.h>

#include <fstream>
#include <iterator>
#include <vector>

#include "asset_pack.h"
#include "boot_guard.h"

static void testBootGuard() {
  moni::BootRecord record = {0, 99};
  assert(!moni::registerBoot(record, false));
  assert(record.crashes == 0);
  assert(!moni::registerBoot(record, true));
  assert(!moni::registerBoot(record, true));
  assert(moni::registerBoot(record, true));
  assert(!moni::registerBoot(record, false));  // 정상 재시작이면 안전 모드를 벗어난다
  moni::BootRecord garbage = {123, 7};
  assert(!moni::registerBoot(garbage, true));
  assert(garbage.crashes == 1);
}

static void testPack(const char* path) {
  std::ifstream file(path, std::ios::binary);
  std::vector<uint8_t> blob((std::istreambuf_iterator<char>(file)), {});
  moni::PackHeader header;
  assert(moni::parseHeader(blob.data(), header));
  assert(header.emotionCount == 2 && header.width == 120 && header.height == 120);

  const uint8_t* body = blob.data() + moni::kPackHeaderSize;
  size_t bodySize = blob.size() - moni::kPackHeaderSize;
  assert(moni::crc32Update(0, body, bodySize) == header.crc);

  // 나눠서 계산해도 같아야 한다(기기는 256바이트씩 읽는다).
  uint32_t crc = moni::crc32Update(0, body, 5);
  crc = moni::crc32Update(crc, body + 5, bodySize - 5);
  assert(crc == header.crc);

  moni::PackEmotion second;
  moni::parseEmotion(body + moni::kPackEmotionSize, second);
  assert(strcmp(second.name, "think") == 0);
  assert(second.frames == 1 && second.fps == 8 && second.firstFrame == 2);

  size_t table = moni::kPackHeaderSize + moni::kPackEmotionSize * 2;
  moni::PackFrame frame;
  moni::parseFrame(blob.data() + table + moni::kPackFrameSize * 2, frame);
  assert(frame.length == 3);
  assert(memcmp(blob.data() + frame.offset, "ccc", 3) == 0);

  blob[0] = 'X';
  assert(!moni::parseHeader(blob.data(), header));
}

int main(int argc, char** argv) {
  assert(argc == 2);
  testBootGuard();
  testPack(argv[1]);
  puts("native ok");
}
