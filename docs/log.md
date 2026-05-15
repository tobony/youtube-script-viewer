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
