# 변경 로그

## 2026-05-15

### APP_PORT 기본값 통일

`uv run main.py`와 `docker compose up` 모두에서 API 경로 불일치가 발생하지 않도록 포트 설정을 통일.

**문제**: `main.py`는 기본 8080, `ui.py`의 API_BASE는 기본 7030으로 달라서 로컬 실행 시 API 호출 실패.

**변경 내용**:
- `app/ui.py`: API_BASE 기본값 `7030` → `8080`
- `docker-compose.yml`: `APP_PORT=7030` 환경변수 추가
- `Dockerfile`: `EXPOSE 8080` → `EXPOSE 7030`

**결과**:
| 실행 방식 | APP_PORT | 접속 포트 |
|-----------|----------|-----------|
| `uv run main.py` | 미설정 → 8080 | localhost:8080 |
| `docker compose up` | 7030 (환경변수) | localhost:7030 |


### Docker에서 분석 실행 시 연결 끊김 수정

**문제**: Docker 환경에서 YouTube URL 입력 후 "분석" 버튼 클릭 시 "trying to reconnect" 메시지와 함께 `ERR_CONNECTION_REFUSED` 발생.

**원인**:
1. `host` 미지정 — NiceGUI 기본 바인딩이 `127.0.0.1`이라 컨테이너 외부에서 접근 불가
2. `reload=True` — 파이프라인 실행 중 파일 변경(DB 등)이 서버 reload를 트리거하여 WebSocket 연결 끊김

**변경 내용**:
- `main.py`: `host="0.0.0.0"` 추가, `reload`를 `APP_ENV != "docker"` 조건으로 변경
- `docker-compose.yml`: `APP_ENV=docker` 환경변수 추가
