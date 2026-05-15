# YouTube Script Viewer

YouTube 영상의 transcript를 추출하고, AI(Claude)를 활용해 한국어 번역 및 요약을 제공하는 웹 앱.

## 사전 요구사항: kiro-gateway

이 프로젝트는 LLM 번역/요약을 위해 [kiro-gateway](https://github.com/jwadow/kiro-gateway)를 사용합니다.
kiro-gateway는 독립 프로젝트로 Docker 상시 실행합니다.

```bash
cd ~/kiro-gateway
docker compose -f docker-compose.override.yml up -d
```

| 항목 | 값 |
|------|-----|
| 포트 | `localhost:4000` (충돌 없는 대역) |
| 재시작 정책 | `unless-stopped` (WSL 재시작 시 자동 복구) |
| API 엔드포인트 | `http://localhost:4000/v1` |
| API Key | `kiro-local` |
| 인증 소스 | `~/.local/share/kiro-cli/data.sqlite3` |

> 모든 프로젝트에서 `http://localhost:4000/v1`로 Claude 모델 사용 가능.
> 토큰 만료 시 호스트에서 `kiro-cli login` 재실행.

---

## 아키텍처

```
[NiceGUI 앱 :8080] → [Pipeline] → [yt-dlp / youtube-transcript-api]
                                 → [kiro-gateway :4000] → [Claude Haiku 4.5]
```

## 실행 방법

```bash
cd /home/ubuntu/repo/youtube-script
uv run backend/main.py
```

- UI: http://localhost:8080
- Hot reload 활성화

## kiro-gateway 활용 (다른 프로젝트에서도 사용)

kiro-gateway는 OpenAI-compatible API를 제공하므로 어떤 프로젝트에서든:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:4000/v1",
    api_key="kiro-local",
)

response = client.chat.completions.create(
    model="claude-haiku-4-5",
    messages=[{"role": "user", "content": "Hello!"}],
)
```

### 사용 가능 모델 (무료 티어)
- `claude-haiku-4-5` — 빠른 응답, 간단한 작업
- `claude-sonnet-4-5` — 균형 잡힌 성능
- `claude-sonnet-4` — 이전 세대
- `deepseek-v3-2` — 오픈 MoE 모델

### Docker 관리

```bash
# 상태 확인
docker ps | grep kiro-gateway

# 로그 확인
docker logs kiro-gateway

# 재시작
docker compose -f docker-compose.override.yml restart

# 중지
docker compose -f docker-compose.override.yml down
```

## 프로젝트 구조

| 파일 | 역할 |
|------|------|
| `backend/main.py` | 엔트리포인트 |
| `backend/app/ui.py` | 웹 UI |
| `backend/app/pipeline.py` | 비동기 파이프라인 |
| `backend/app/youtube.py` | 메타데이터 + transcript 추출 |
| `backend/app/llm.py` | kiro-gateway 연동 (번역/요약) |
| `backend/app/db.py` | SQLite DB |
| `backend/app/routers.py` | REST API |

## 의존성

- `yt-dlp`: YouTube 메타데이터
- `youtube-transcript-api`: 자막 추출
- `nicegui`: 웹 UI
- `openai`: kiro-gateway API 호출
- `aiosqlite`: 비동기 SQLite
