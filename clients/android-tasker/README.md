# 안드로이드 Tasker 태스크 만들기

`<맥북>`은 브리지가 도는 기기 이름, `<토큰>`은 `bridge/.env`의 `MONI_CLIENT_TOKEN`이다.

1. `HTTP Request`: POST `http://<맥북>.local:8765/v1/listen`, 헤더 `Authorization:Bearer <토큰>`, 본문 `{"client":"android"}`
2. `Get Voice`: 언어 ko-KR
3. `HTTP Request`: POST `http://<맥북>.local:8765/v1/command`, 같은 헤더, 본문 `{"text":"%gv_heard","client":"android","lang":"ko-KR"}`
4. `Say`: `%http_data.speak`, 엔진 한국어
5. 3단계 실패 시 `Say`: "모니가 자는 중이에요"
6. 태스크를 빠른 설정 타일에 연결

폰에서 `.local` 이름이 풀리지 않으면 맥북 IP를 직접 쓴다.
