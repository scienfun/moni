// 모니 펌웨어: 표정은 기기가 스스로, 글자는 브리지가 그린 점으로.
#include <Arduino.h>
#include <ArduinoJson.h>
#include <ArduinoOTA.h>
#include <ESP8266HTTPUpdateServer.h>
#include <ESP8266WebServer.h>
#include <ESP8266WiFi.h>
#include <ESP8266mDNS.h>
#include <LittleFS.h>
#include <TFT_eSPI.h>
#include <TJpg_Decoder.h>
#include <WiFiManager.h>
#include <time.h>

#include "asset_pack.h"
#include "boot_guard.h"

#define MONI_VERSION "0.3.0"

namespace {

const char kHostname[] = "moni";
const char kPackPath[] = "/moni-assets.bin";
const char kPackTmpPath[] = "/moni-assets.tmp";
const char kTimezone[] = "KST-9";

const int kSpriteSize = 120;
const int kSpriteLeftX = 4;
const int kSpriteCenterX = 60;
const int kSpriteY = 8;
const int kPanelX = 128;
const int kPanelW = 108;
const int kPanelH = 120;
const int kBubbleY = 136;
const int kBubbleW = 240;
const int kBubbleH = 104;
const size_t kBubbleBytes = 3120;
const size_t kPanelMaxBytes = 12 * 1024;
const uint32_t kMinFreeHeap = 20 * 1024;
const uint32_t kStableAfterMs = 30 * 1000;
const uint32_t kRtcSlot = 32;
const uint16_t kWifiPortalSeconds = 300;

TFT_eSPI tft;
ESP8266WebServer server(80);
ESP8266HTTPUpdateServer updater;

// 에셋팩
File pack;
bool packReady = false;
moni::PackEmotion emotions[moni::kMaxEmotions];
uint8_t emotionCount = 0;
uint32_t frameTableAt = 0;

// 화면 상태
int8_t currentEmotion = -1;
uint8_t currentFrame = 0;
uint32_t lastFrameAt = 0;
bool panelActive = false;
bool idleShown = false;
int lastClockMinute = -1;
uint32_t idleAt = 0;
char idleEmotion[11] = "idle";
uint8_t brightness = 200;
bool stableMarked = false;

// 요청 본문 처리
uint8_t bubbleBuffer[kBubbleBytes];
size_t bodyReceived = 0;
int bodyError = 0;
bool bodyDone = false;
uint8_t* panelBuffer = nullptr;
size_t panelCapacity = 0;
File uploadFile;

void setBacklight(uint8_t value) {
#if MONI_BACKLIGHT_ACTIVE_LOW
  analogWrite(MONI_BACKLIGHT_PIN, 255 - value);
#else
  analogWrite(MONI_BACKLIGHT_PIN, value);
#endif
}

bool tftOutput(int16_t x, int16_t y, uint16_t w, uint16_t h, uint16_t* bitmap) {
  tft.pushImage(x, y, w, h, bitmap);
  return true;
}

int spriteX() { return panelActive ? kSpriteLeftX : kSpriteCenterX; }

void closePack() {
  if (pack) pack.close();
  packReady = false;
  currentEmotion = -1;
}

bool openPack() {
  closePack();
  pack = LittleFS.open(kPackPath, "r");
  if (!pack) return false;

  uint8_t raw[moni::kPackHeaderSize];
  moni::PackHeader header;
  if (pack.read(raw, sizeof(raw)) != sizeof(raw) ||
      !moni::parseHeader(raw, header)) {
    pack.close();
    return false;
  }
  emotionCount = header.emotionCount;
  for (uint8_t i = 0; i < emotionCount; i++) {
    uint8_t entry[moni::kPackEmotionSize];
    if (pack.read(entry, sizeof(entry)) != sizeof(entry)) {
      pack.close();
      return false;
    }
    moni::parseEmotion(entry, emotions[i]);
  }
  frameTableAt = moni::kPackHeaderSize + moni::kPackEmotionSize * emotionCount;
  packReady = true;
  return true;
}

int findEmotion(const char* name) {
  for (uint8_t i = 0; i < emotionCount; i++) {
    if (strcmp(emotions[i].name, name) == 0) return i;
  }
  return -1;
}

bool setEmotion(const char* name) {
  int index = findEmotion(name);
  if (index < 0) return false;
  currentEmotion = index;
  currentFrame = 0;
  lastFrameAt = 0;
  return true;
}

void drawFrame() {
  const moni::PackEmotion& emotion = emotions[currentEmotion];
  uint8_t raw[moni::kPackFrameSize];
  moni::PackFrame frame;
  uint32_t index = emotion.firstFrame + currentFrame;
  pack.seek(frameTableAt + index * moni::kPackFrameSize, SeekSet);
  if (pack.read(raw, sizeof(raw)) != sizeof(raw)) return;
  moni::parseFrame(raw, frame);
  if (frame.length == 0 || frame.length > moni::kMaxFrameBytes) return;

  uint8_t* jpeg = static_cast<uint8_t*>(malloc(frame.length));
  if (jpeg == nullptr) return;
  pack.seek(frame.offset, SeekSet);
  if (pack.read(jpeg, frame.length) == static_cast<int>(frame.length)) {
    TJpgDec.drawJpg(spriteX(), kSpriteY, jpeg, frame.length);
  }
  free(jpeg);
}

void tickAnimation() {
  if (!packReady || currentEmotion < 0) return;
  const moni::PackEmotion& emotion = emotions[currentEmotion];
  uint32_t now = millis();
  uint32_t interval = 1000 / max<uint8_t>(emotion.fps, 1);
  if (lastFrameAt != 0 && now - lastFrameAt < interval) return;
  lastFrameAt = now;
  drawFrame();
  currentFrame = (currentFrame + 1) % max<uint8_t>(emotion.frames, 1);
}

void setPanelActive(bool active) {
  if (active == panelActive) return;
  panelActive = active;
  tft.fillRect(0, 0, 240, kBubbleY, TFT_BLACK);
  lastFrameAt = 0;
}

void clearBubble() { tft.fillRect(0, kBubbleY, kBubbleW, kBubbleH, TFT_BLACK); }

// 말풍선은 한 줄씩 변환해 흘려보낸다. 240×240 프레임버퍼는 만들 수 없다.
void drawBubble() {
  uint16_t line[kBubbleW];
  const size_t rowBytes = kBubbleW / 8;
  tft.startWrite();
  tft.setAddrWindow(0, kBubbleY, kBubbleW, kBubbleH);
  for (int y = 0; y < kBubbleH; y++) {
    const uint8_t* row = bubbleBuffer + y * rowBytes;
    for (int x = 0; x < kBubbleW; x++) {
      bool ink = row[x >> 3] & (0x80 >> (x & 7));
      line[x] = ink ? TFT_WHITE : TFT_BLACK;
    }
    tft.pushPixels(line, kBubbleW);
    yield();
  }
  tft.endWrite();
}

void drawClock() {
  time_t now = time(nullptr);
  if (now < 1700000000) return;  // NTP 동기화 전
  struct tm local;
  localtime_r(&now, &local);
  if (local.tm_min == lastClockMinute) return;
  lastClockMinute = local.tm_min;

  char text[6];
  snprintf(text, sizeof(text), "%02d:%02d", local.tm_hour, local.tm_min);
  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.drawString(text, 120, 188, 7);
}

void enterIdle() {
  idleAt = 0;
  idleShown = true;
  lastClockMinute = -1;
  setEmotion(idleEmotion);
  setPanelActive(false);
  clearBubble();
  drawClock();
}

void tickIdle() {
  if (idleAt != 0 && int32_t(millis() - idleAt) >= 0) enterIdle();
  if (idleShown) drawClock();
}

void showStatus(const char* title, const String& detail) {
  clearBubble();
  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.drawString(title, 120, 165, 4);
  tft.drawString(detail, 120, 200, 2);
}

void markStable() {
  moni::BootRecord record = {moni::kBootMagic, 0};
  ESP.rtcUserMemoryWrite(kRtcSlot, reinterpret_cast<uint32_t*>(&record),
                         sizeof(record));
}

bool authorized() {
  return server.header("X-Moni-Token") == F(MONI_DEVICE_TOKEN);
}

bool heapOk() { return ESP.getFreeHeap() >= kMinFreeHeap; }

// 본문이 없는 요청과 JSON 요청의 공통 검사
bool guard() {
  if (!authorized()) {
    server.send(401, "application/json", "{}");
    return false;
  }
  if (!heapOk()) {
    server.send(503, "application/json", "{}");
    return false;
  }
  return true;
}

// 바이너리 본문은 multipart로 받는다. 이 코어에서 RAM에 다 올리지 않고
// 스트리밍되는 방식은 업로드 콜백뿐이다.
bool startBody() {
  bodyReceived = 0;
  bodyError = 0;
  bodyDone = false;
  if (!authorized()) {
    bodyError = 401;
  } else if (!heapOk()) {
    bodyError = 503;
  }
  return bodyError == 0;
}

// 업로드 콜백이 끝난 뒤 불리는 최종 핸들러의 공통 판정
int finishBody() {
  int status = 200;
  if (!authorized()) {
    status = 401;
  } else if (bodyError != 0) {
    status = bodyError;
  } else if (!bodyDone) {
    status = 400;  // multipart가 아닌 요청
  }
  bodyDone = false;
  return status;
}

void sendStatus(int status) {
  server.send(status, "application/json", "{}");
}

void handleStatus() {
  // 힙이 부족할 때도 진단할 수 있도록 토큰만 검사한다.
  if (!authorized()) {
    server.send(401, "application/json", "{}");
    return;
  }
  JsonDocument doc;
  doc["fw"] = MONI_VERSION;
  doc["heap"] = ESP.getFreeHeap();
  doc["max_block"] = ESP.getMaxFreeBlockSize();
  doc["rssi"] = WiFi.RSSI();
  doc["ip"] = WiFi.localIP().toString();
  doc["emotion"] = currentEmotion >= 0 ? emotions[currentEmotion].name : "";
  doc["assets"] = packReady;
  doc["uptime"] = millis() / 1000;
  String body;
  serializeJson(doc, body);
  server.send(200, "application/json", body);
}

void handleEmotion() {
  if (!guard()) return;
  JsonDocument doc;
  if (deserializeJson(doc, server.arg("plain")) != DeserializationError::Ok) {
    server.send(400, "application/json", "{}");
    return;
  }
  const char* name = doc["name"] | "";
  server.send(setEmotion(name) ? 200 : 400, "application/json", "{}");
}

void handleBrightness() {
  if (!guard()) return;
  JsonDocument doc;
  if (deserializeJson(doc, server.arg("plain")) != DeserializationError::Ok) {
    server.send(400, "application/json", "{}");
    return;
  }
  brightness = constrain(doc["value"] | 0, 0, 255);
  setBacklight(brightness);
  server.send(200, "application/json", "{}");
}

void handleIdle() {
  if (!guard()) return;
  uint32_t after = server.arg("after").toInt();
  String emotion = server.arg("emotion");
  if (emotion.length() > 0 && findEmotion(emotion.c_str()) >= 0) {
    strlcpy(idleEmotion, emotion.c_str(), sizeof(idleEmotion));
  }
  idleAt = (millis() + after * 1000) | 1;  // 0은 "예약 없음"이다
  server.send(200, "application/json", "{}");
}

void receiveBubble() {
  HTTPUpload& upload = server.upload();
  if (upload.status == UPLOAD_FILE_START) {
    startBody();
  } else if (upload.status == UPLOAD_FILE_WRITE && bodyError == 0) {
    if (bodyReceived + upload.currentSize > kBubbleBytes) {
      bodyError = 400;
    } else {
      memcpy(bubbleBuffer + bodyReceived, upload.buf, upload.currentSize);
      bodyReceived += upload.currentSize;
    }
  } else if (upload.status == UPLOAD_FILE_END) {
    bodyDone = true;
  }
}

void finishBubble() {
  int status = finishBody();
  if (status == 200 && bodyReceived != kBubbleBytes) status = 400;
  if (status == 200) {
    idleShown = false;
    drawBubble();
  }
  sendStatus(status);
}

void freePanelBuffer() {
  free(panelBuffer);
  panelBuffer = nullptr;
}

void receivePanel() {
  HTTPUpload& upload = server.upload();
  if (upload.status == UPLOAD_FILE_START) {
    if (startBody()) {
      // contentLength는 multipart 경계까지 포함하므로 파일보다 약간 크다.
      panelCapacity = min<size_t>(upload.contentLength, kPanelMaxBytes);
      panelBuffer = static_cast<uint8_t*>(malloc(panelCapacity));
      if (panelBuffer == nullptr) bodyError = 503;
    }
  } else if (upload.status == UPLOAD_FILE_WRITE && bodyError == 0) {
    if (bodyReceived + upload.currentSize > panelCapacity) {
      bodyError = 400;
    } else {
      memcpy(panelBuffer + bodyReceived, upload.buf, upload.currentSize);
      bodyReceived += upload.currentSize;
    }
  } else if (upload.status == UPLOAD_FILE_END) {
    bodyDone = true;
  } else if (upload.status == UPLOAD_FILE_ABORTED) {
    freePanelBuffer();
  }
}

void finishPanel() {
  int status = finishBody();
  if (status == 200 && bodyReceived == 0) status = 400;
  if (status == 200) {
    setPanelActive(true);
    int x = server.hasArg("x") ? server.arg("x").toInt() : kPanelX;
    int y = server.hasArg("y") ? server.arg("y").toInt() : kSpriteY;
    TJpgDec.drawJpg(x, y, panelBuffer, bodyReceived);
  }
  freePanelBuffer();
  sendStatus(status);
}

bool verifyPackFile(const char* path) {
  File file = LittleFS.open(path, "r");
  if (!file) return false;
  uint8_t chunk[256];
  moni::PackHeader header;
  bool valid = file.read(chunk, moni::kPackHeaderSize) == moni::kPackHeaderSize &&
               moni::parseHeader(chunk, header);
  uint32_t crc = 0;
  while (valid && file.available()) {
    size_t count = file.read(chunk, sizeof(chunk));
    crc = moni::crc32Update(crc, chunk, count);
    yield();
  }
  file.close();
  return valid && crc == header.crc;
}

void reloadPack() {
  LittleFS.remove(kPackTmpPath);
  openPack();
  setEmotion(idleEmotion);
}

void receiveAssets() {
  HTTPUpload& upload = server.upload();
  if (upload.status == UPLOAD_FILE_START) {
    if (startBody()) {
      closePack();
      uploadFile = LittleFS.open(kPackTmpPath, "w");
      if (!uploadFile) bodyError = 500;
    }
  } else if (upload.status == UPLOAD_FILE_WRITE && bodyError == 0) {
    if (uploadFile.write(upload.buf, upload.currentSize) != upload.currentSize) {
      bodyError = 507;
    }
  } else if (upload.status == UPLOAD_FILE_END) {
    if (uploadFile) uploadFile.close();
    bodyDone = true;
  } else if (upload.status == UPLOAD_FILE_ABORTED) {
    if (uploadFile) uploadFile.close();
    reloadPack();
  }
}

void finishAssets() {
  int status = finishBody();
  if (status == 200) {
    if (verifyPackFile(kPackTmpPath)) {
      LittleFS.remove(kPackPath);
      LittleFS.rename(kPackTmpPath, kPackPath);
    } else {
      status = 400;
    }
  }
  // 인증 실패 시에는 시작 단계에서 닫지 않았으므로 건드리지 않는다.
  if (status != 401) reloadPack();
  sendStatus(status);
}

void setupRoutes() {
  server.collectHeaders("X-Moni-Token");

  server.on("/api/status", HTTP_GET, handleStatus);
  server.on("/api/emotion", HTTP_POST, handleEmotion);
  server.on("/api/brightness", HTTP_POST, handleBrightness);
  server.on("/api/idle", HTTP_POST, handleIdle);
  server.on("/api/bubble", HTTP_POST, finishBubble, receiveBubble);
  server.on("/api/panel", HTTP_POST, finishPanel, receivePanel);
  server.on("/api/assets", HTTP_POST, finishAssets, receiveAssets);
  server.on("/api/bubble", HTTP_DELETE, [] {
    if (!guard()) return;
    clearBubble();
    server.send(200, "application/json", "{}");
  });
  server.on("/api/panel", HTTP_DELETE, [] {
    if (!guard()) return;
    setPanelActive(false);
    server.send(200, "application/json", "{}");
  });
#ifdef MONI_DEBUG_CRASH
  server.on("/api/debug/crash", HTTP_POST, [] {
    if (!guard()) return;
    server.send(200, "application/json", "{}");
    server.client().flush();
    delay(50);
    *reinterpret_cast<volatile uint32_t*>(0) = 1;
  });
#endif
  server.onNotFound([] { server.send(404, "application/json", "{}"); });
}

void setupOta() {
  ArduinoOTA.setHostname(kHostname);
  ArduinoOTA.setPassword(MONI_OTA_PASSWORD);
  ArduinoOTA.onStart([] {
    closePack();
    showStatus("Updating", "do not power off");
  });
  ArduinoOTA.onError([](ota_error_t) { ESP.restart(); });
  ArduinoOTA.begin();
  MDNS.addService("http", "tcp", 80);
}

void runSafeMode() {
  WiFi.mode(WIFI_AP);
  WiFi.softAP("Moni-Recovery");
  updater.setup(&server, "/update", "moni", MONI_OTA_PASSWORD);
  server.begin();
  showStatus("Safe mode", "Moni-Recovery / 192.168.4.1/update");
  setBacklight(brightness);
  while (true) {
    server.handleClient();
    delay(2);
  }
}

void connectWifi() {
  WiFiManager manager;
  manager.setConfigPortalTimeout(kWifiPortalSeconds);
  manager.setAPCallback([](WiFiManager*) {
    // 포털까지 왔다면 부팅 자체는 정상이다.
    markStable();
    showStatus("Moni-Setup", "connect to set up Wi-Fi");
    setBacklight(brightness);
  });
  if (!manager.autoConnect("Moni-Setup")) {
    markStable();
    ESP.restart();
  }
  WiFi.setAutoReconnect(true);
}

}  // namespace

void setup() {
  Serial.begin(115200);
  pinMode(MONI_BACKLIGHT_PIN, OUTPUT);
  analogWriteRange(255);
  setBacklight(0);

  moni::BootRecord record;
  ESP.rtcUserMemoryRead(kRtcSlot, reinterpret_cast<uint32_t*>(&record),
                        sizeof(record));
  uint32_t reason = ESP.getResetInfoPtr()->reason;
  bool crashed = reason == REASON_WDT_RST || reason == REASON_EXCEPTION_RST ||
                 reason == REASON_SOFT_WDT_RST;
  bool safeMode = moni::registerBoot(record, crashed);
  ESP.rtcUserMemoryWrite(kRtcSlot, reinterpret_cast<uint32_t*>(&record),
                         sizeof(record));

  tft.init();
  tft.setRotation(0);
  tft.setSwapBytes(true);
  tft.fillScreen(TFT_BLACK);

  if (safeMode) runSafeMode();

  TJpgDec.setJpgScale(1);
  TJpgDec.setSwapBytes(false);
  TJpgDec.setCallback(tftOutput);

  LittleFS.begin();
  openPack();
  connectWifi();

  configTime(kTimezone, "pool.ntp.org", "time.google.com");
  setupOta();
  setupRoutes();
  updater.setup(&server, "/update", "moni", MONI_OTA_PASSWORD);
  server.begin();

  setEmotion("idle");
  showStatus("moni.local", WiFi.localIP().toString());
  setBacklight(brightness);
}

void loop() {
  server.handleClient();
  ArduinoOTA.handle();
  tickIdle();
  tickAnimation();

  if (!stableMarked && millis() > kStableAfterMs) {
    stableMarked = true;
    markStable();
  }
}
