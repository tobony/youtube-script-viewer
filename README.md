# YouTube Script Viewer

YouTube 영상의 transcript를 추출하고, AI(Claude/GPT)를 활용해 한국어 번역 및 요약을 제공하는 웹 앱.

## 사전 요구사항: kiro-gateway (선택)

LLM 번역/요약을 위해 [kiro-gateway](https://github.com/jwadow/kiro-gateway)를 Docker로 상시 실행합니다.

```bash
cd ~/kiro-gateway
docker compose -f docker-compose.override.yml up -d
```

| 항목 | 값 |
|------|-----|
| 포트 | `localhost:4000` |
| 재시작 정책 | `unless-stopped` (WSL 재시작 시 자동 복구) |
| API 엔드포인트 | `http://localhost:4000/v1` |
| API Key | `kiro-local` |
| 인증 소스 | `~/.local/share/kiro-cli/data.sqlite3` |

> 모든 프로젝트에서 `http://localhost:4000/v1`로 Claude 모델 사용 가능.
> 토큰 만료 시 호스트에서 `kiro-cli login` 재실행.

---

## 실행 방법

### 방법 1: 로컬 실행 (개발용)

```bash
cd ~/repo/youtube-script
uv run main.py
```

- UI: http://localhost:8080
- Hot reload 활성화

### 방법 2: Docker 실행

```bash
cd ~/repo/youtube-script

# 빌드 & 실행
docker compose up -d --build

# 로그 확인
docker logs youtube-script -f

# 중지
docker compose down
```

| 항목 | 설명 |
|------|------|
| 포트 | `localhost:8080` |
| DB | `./data/youtube_scripts.db` (호스트와 volume 공유) |
| kiro-gateway | `host.docker.internal:4000` (Docker에서 호스트 접근) |
| Azure OpenAI | `.env`에서 설정 (이미지에 포함) |

#### 코드 수정 후 업데이트

```bash
# 재빌드 (코드 변경 시)
docker compose up -d --build

# 의존성 변경 없을 때 (캐시 활용, 빠름)
docker compose build && docker compose up -d
```

#### Docker에서 kiro-gateway 사용 시

`.env`의 `KIRO_BASE_URL`을 변경:
```
KIRO_BASE_URL=http://host.docker.internal:4000/v1
```

---

## 아키텍처

```
[NiceGUI 앱 :8080] → [Pipeline] → [yt-dlp / youtube-transcript-api]
                                 → [Azure OpenAI / kiro-gateway] → [GPT / Claude]
```

## LLM 설정 (.env)

`.env` 파일에서 LLM 프로바이더와 모델을 설정합니다:

```env
# Provider: "azure" 또는 "kiro"
LLM_PROVIDER=azure

# Azure OpenAI
AZURE_API_KEY=your-key
AZURE_ENDPOINT=https://your-resource.services.ai.azure.com/models
AZURE_MODEL=gpt-5.4-nano

# Kiro Gateway
KIRO_BASE_URL=http://localhost:4000/v1
KIRO_API_KEY=kiro-local
KIRO_MODEL=claude-haiku-4-5
```

UI에서도 런타임에 프로바이더를 전환할 수 있습니다.

### 개발용 샘플 DB

기본 DB인 `data/youtube_scripts.db`가 없으면 앱 시작 시
`sample_data/youtube_scripts.db`를 자동으로 복사해 샘플 목록을 표시합니다.
이미 기본 DB가 있거나 `DB_PATH`를 다른 경로로 지정한 경우에는 기존 DB를
덮어쓰거나 샘플 데이터를 추가하지 않습니다.

## kiro-gateway 활용 (다른 프로젝트에서도 사용)

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
- `deepseek-v3-2` — 오픈 MoE 모델

### kiro-gateway Docker 관리

```bash
cd ~/kiro-gateway

# 상태 확인
docker ps | grep kiro-gateway

# 로그
docker logs kiro-gateway

# 재시작
docker compose -f docker-compose.override.yml restart

# 중지
docker compose -f docker-compose.override.yml down
```

## 프로젝트 구조

```
youtube-script/
├── main.py              ← 엔트리포인트 (uv run main.py)
├── app/                 ← 앱 코드
│   ├── ui.py            ← 웹 UI
│   ├── pipeline.py      ← 비동기 파이프라인
│   ├── youtube.py       ← 메타데이터 + transcript 추출
│   ├── llm.py           ← LLM 연동 (번역/요약)
│   ├── db.py            ← SQLite DB
│   ├── routers.py       ← REST API
│   └── models.py        ← Pydantic 모델
├── tests/
├── data/                ← SQLite DB (gitignore)
├── docs/
├── .env                 ← LLM 설정 (gitignore)
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
└── .gitignore
```

## 의존성

- `yt-dlp`: YouTube 메타데이터
- `youtube-transcript-api`: 자막 추출
- `nicegui`: 웹 UI
- `openai`: Azure OpenAI / kiro-gateway API 호출
- `aiosqlite`: 비동기 SQLite
- `python-dotenv`: 환경변수 관리
