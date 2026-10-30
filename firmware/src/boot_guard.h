// 연속 크래시 감지. RTC 메모리에 기록해 재부팅 사이에 유지한다.
#pragma once

#include <stdint.h>

namespace moni {

const uint32_t kBootMagic = 0x4D4F4E49;  // "MONI"
const uint32_t kCrashLimit = 3;

struct BootRecord {
  uint32_t magic;
  uint32_t crashes;
};

// crashed: 이번 부팅이 예외·워치독 리셋으로 시작했는지.
// 전원 투입이나 정상 재시작이면 기록을 비운다. 반환값이 true면 안전 모드.
inline bool registerBoot(BootRecord& record, bool crashed) {
  if (record.magic != kBootMagic || !crashed) {
    record.magic = kBootMagic;
    record.crashes = 0;
  }
  if (crashed) record.crashes++;
  return record.crashes >= kCrashLimit;
}

}  // namespace moni
