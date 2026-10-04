# 🚀 Marketing AI Agent (Python Backend)

**All-Ligo 로컬 AI 마케팅 에이전트**의 AI 생성 서버입니다. Spring 백엔드(`All-Ligo_Was`)의 요청을 받아 콘텐츠를 만들고 웹훅으로 결과를 돌려줍니다.

이곳의 코드는 사용자의 요청을 받아 로컬 AI 모델을 호출하고, 그 결과(텍스트, 이미지, 영상)를 가공하여 API로 제공하는 역할을 담당합니다. FastAPI 프레임워크를 기반으로 제작되었습니다.

## 📂 프로젝트 구조

일반적인 FastAPI 프로젝트 관례에 따라 **라우팅 / 스키마 / 비즈니스 로직**을 계층으로 분리했습니다.

```
.
├── main.py                     # 하위 호환용 진입점 (app.main:app 재노출)
├── requirements.txt
│
├── app/
│   ├── main.py                 # FastAPI 앱 조립 (메타데이터, 정적 마운트, 라우터 등록)
│   │
│   ├── core/                   # 애플리케이션 기반 설정
│   │   ├── config.py           # 환경 변수·경로 상수 단일 소스 (settings)
│   │   ├── logging_config.py   # 로깅 초기화
│   │   └── state.py            # 동시 작업 수 추적 (JobTracker)
│   │
│   ├── api/                    # HTTP 라우팅 계층 (요청 검증 / 응답 변환만)
│   │   ├── router.py           # 라우터 집합
│   │   └── routes/
│   │       ├── marketing.py    # 콘텐츠 생성 + YouTube 업로드
│   │       ├── weather.py      # 실시간 날씨 조회
│   │       ├── system.py       # 헬스 체크 (GPU·디스크·작업 수) + 기동 점검
│   │       ├── vision.py       # 이미지 분석
│   │       └── home.py         # 접속 확인 페이지
│   │
│   ├── schemas/                # 요청/응답 Pydantic 모델
│   │   ├── content.py          # GenerateRequestDto, JobAcceptedResponse, ContentResult
│   │   ├── youtube.py          # UploadRequest, UploadResponse
│   │   ├── weather.py          # WeatherInfo
│   │   └── system.py           # GpuStatus, SystemStatusResponse
│   │
│   ├── services/               # 비즈니스 로직 계층
│   │   ├── content_pipeline.py # 콘텐츠 생성 백그라운드 파이프라인 (전체 흐름 조립)
│   │   ├── weather_service.py  # Open-Meteo 날씨 조회
│   │   ├── system_service.py   # GPU/디스크 상태 및 가용 여부 판정
│   │   ├── vision_service.py   # LLaVA 이미지 분석
│   │   ├── image_service.py    # FLUX / SDXL 포스터 이미지 생성
│   │   ├── storage_service.py  # AWS S3 업로드
│   │   ├── youtube_service.py  # YouTube 업로드
│   │   ├── image_fetcher.py    # 요청 이미지 URL 병렬 다운로드
│   │   ├── text_cleaner.py     # LLM 출력 후처리
│   │   ├── webhook_service.py  # Spring 백엔드 결과 통보 (실패 시 failed_webhooks/ 에 보관)
│   │   ├── preflight_service.py# 기동 점검 (의존성·모델 캐시·토큰)
│   │   └── video/              # FFmpeg 숏폼 영상 생성 패키지
│   │       ├── constants.py    # 해상도, Ken Burns 프리셋 등
│   │       ├── ffmpeg_runner.py# FFmpeg 실행 + NVENC→CPU 폴백
│   │       ├── audio.py        # TTS, BGM 선택, 비트 분석
│   │       ├── subtitles.py    # 타이핑 자막 PNG 시퀀스 생성
│   │       ├── compositor.py   # 전처리 / 세그먼트 / 트랜지션 / 최종 합성
│   │       └── renderer.py     # 위 단계 조립 오케스트레이터
│   │
│   ├── prompts/
│   │   └── marketing.py        # LLM 프롬프트 템플릿 조립
│   │
│   └── templates/
│       └── home.html           # 접속 확인 페이지
│
├── scripts/                    # 운영용 스크립트
│   ├── refresh_youtube_token.py        # OAuth 토큰 발급 (브라우저 자동)
│   └── refresh_youtube_token_manual.py # OAuth 토큰 발급 (URL 수동 입력)
│
├── tools/                      # 개발 중 손으로 돌려보는 점검 도구
│   ├── check_ollama.py
│   ├── check_youtube_upload.py
│   └── resend_failed_webhooks.py # 전송 실패한 웹훅 결과 재전송
│
├── tests/                      # pytest 스모크 테스트 (외부 서비스 없이 실행)
└── docs/                       # 프로젝트 개요·작업 계획·현황
```

### 계층 규칙

새 기능을 추가할 때 아래 규칙을 지키면 구조가 유지됩니다.

| 계층 | 하는 일 | 하지 말아야 할 일 |
|------|---------|------------------|
| `api/routes/` | 요청 검증, 서비스 호출, 응답 변환 | AI 호출·파일 조작 같은 실제 처리 |
| `services/` | 실제 처리 (AI, 파일, 외부 API) | `HTTPException` 던지기, 라우팅 관심사 |
| `schemas/` | 입출력 형태 정의 | 비즈니스 로직 |
| `core/config.py` | 환경 변수 읽기 | — (다른 모듈에서 `os.getenv` 직접 호출 금지) |

## 🔄 콘텐츠 생성 흐름

```
Spring 백엔드
   │  POST /api/marketing/generate
   ▼
routes/marketing.py ──► 즉시 202 Accepted + taskId 반환
   │  (BackgroundTasks 등록)
   ▼
services/content_pipeline.py
   ├─ 1. mode 해석 (AUTO → ORIGINAL / TRANSFORM)
   ├─ 2. 업로드 이미지 분석 (vision_service)
   ├─ 3. 마케팅 텍스트 생성 (Ollama)
   ├─ 4. 포스터 이미지 생성 (image_service)   ※ TRANSFORM + VIDEO 일 때만
   ├─ 5. 숏폼 영상 렌더링 (services/video) + S3 업로드
   └─ 6. 결과 웹훅 전송 (webhook_service) ──► Spring 백엔드
```

## 🛠️ 로컬에서 실행해보기 (Step-by-Step)

### 1. 사전 준비: 필수 프로그램 설치

먼저, 영상 제작에 필요한 `FFmpeg`와 한글 폰트를 설치합니다.

```bash
sudo apt update && sudo apt install -y ffmpeg fonts-noto-cjk
```

### 2. 프로젝트 설정

가상환경을 만들고 라이브러리를 설치합니다.

```bash
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
```

### 3. 필수 디렉토리 생성

`static/` 하위 폴더는 서버 기동 시 자동으로 생성되지만, 폰트 폴더는 직접 만들어 넣어야 합니다.

```bash
mkdir -p fonts static/bgm
```

- `fonts/`: 영상 자막용 폰트(`Pretendard-ExtraBold.otf`). 없으면 시스템 Noto Sans CJK로 폴백합니다.
- `static/bgm/`: 영상 배경음악 mp3. 비어 있으면 BGM 없이 생성됩니다.
- `static/images`, `static/videos`, `static/uploads`: 서버가 자동 생성합니다.

### 4. 환경 변수 설정 (선택)

프로젝트 루트에 `.env` 파일을 만들어 설정을 덮어쓸 수 있습니다. 전체 목록은 [app/core/config.py](app/core/config.py)를 참고하세요.

```bash
SPRING_WEBHOOK_URL=http://localhost:8080/api/internal/content-callback
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_TEXT_MODEL=gemma4:latest
OLLAMA_VISION_MODEL=llava:13b
MAX_CONCURRENT_JOBS=2

AWS_S3_BUCKET=your-bucket-name
AWS_REGION=ap-northeast-2
```

안정성 관련 설정 (기본값 그대로 두는 것을 권장합니다)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `WEBHOOK_MAX_ATTEMPTS` | `3` | 웹훅 일시 실패(연결 오류·5xx) 시 총 시도 횟수 |
| `OLLAMA_TEXT_TIMEOUT_SEC` | `300` | 텍스트 생성 타임아웃 |
| `OLLAMA_TEXT_THINKING` | `false` | gemma4 사고 모드. 켜면 약 2.5배 느려지고 가끔 수 분씩 걸림 |
| `OLLAMA_TEXT_MAX_TOKENS` | `2048` | 생성 토큰 상한 (폭주 방지) |
| `OLLAMA_VISION_TIMEOUT_SEC` | `90` | 이미지 분석 타임아웃 (llava:13b 첫 로드에 30초 이상 걸림) |
| `IMAGE_MODEL_ALLOW_DOWNLOAD` | `false` | `true` 면 캐시에 없는 FLUX/SDXL 을 요청 처리 중에 내려받음 |

### 5. 서버 실행

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

기존 `uvicorn main:app` 명령도 그대로 동작합니다(루트 `main.py`가 앱을 재노출합니다).

서버가 실행되면 `http://localhost:8000/docs` 에서 API 목록을 확인하고 직접 테스트할 수 있습니다.

기동 직후 로그에 `===== 기동 점검 (preflight) =====` 요약이 찍힙니다. Ollama 모델, 이미지 모델 캐시, YouTube 토큰 등에 문제가 있으면 여기서 바로 보입니다. 같은 내용을 `GET /api/system/preflight` 로도 볼 수 있습니다.

> 반드시 이 저장소 루트에서 실행하세요. 상위 폴더에서 실행하면 `Could not import module "main"` 오류가 납니다.

### 6. YouTube 업로드 설정 (선택)

업로드 기능을 쓰려면 Google Cloud Console에서 받은 `client_secret.json`을 프로젝트 루트에 두고 토큰을 발급합니다.

```bash
python scripts/refresh_youtube_token.py
```

브라우저를 띄울 수 없는 원격 서버라면 수동 모드를 사용하세요.

```bash
python scripts/refresh_youtube_token_manual.py
```

## 📡 API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/api/marketing/generate` | 마케팅 콘텐츠 생성 (비동기, 202 + taskId 반환) |
| `POST` | `/api/marketing/upload` | 생성된 영상을 YouTube에 업로드 |
| `GET` | `/api/weather` | 좌표 기반 실시간 날씨 조회 |
| `GET` | `/api/system/status` | 헬스 체크 (GPU·디스크·동시 작업 수) |
| `GET` | `/api/system/preflight` | 기동 점검 (의존성·모델 캐시·토큰 상태) |
| `POST` | `/api/vision/analyze` | 이미지 → 마케팅 키워드 추출 |
| `GET` | `/` | 서버 접속 확인 페이지 |

## ⚠️ 실행 전 확인사항

- **Ollama 서버**: 텍스트 생성과 이미지 분석을 위해 로컬 `Ollama` 서버가 실행 중이어야 합니다. (`http://localhost:11434`)
  ```bash
  ollama pull gemma4:latest && ollama pull llava:13b
  ```
- **Hugging Face 모델 캐시**: 기본 설정에서는 `~/.cache/huggingface/` 에 **이미 있는** 모델만 사용합니다(요청 처리 중 수십 GB 다운로드 방지). FLUX 캐시가 없으면 SDXL 로 생성합니다. 모델을 새로 받으려면 `IMAGE_MODEL_ALLOW_DOWNLOAD=true` 로 한 번 실행하세요.
- **GPU 사양**: VRAM 16GB 이상의 NVIDIA GPU를 권장합니다(현재 운영 머신: RTX 4090 24GB). 이미지 생성은 동시 작업이 있어도 한 번에 하나씩 GPU에 올립니다.
- **FFmpeg NVENC**: GPU 인코딩을 지원하지 않는 FFmpeg 빌드에서는 자동으로 CPU(libx264) 인코딩으로 전환됩니다.

## 🧪 테스트

GPU·Ollama·S3·YouTube·Spring 없이 1~2초 안에 도는 스모크 테스트입니다.

```bash
pip install -r requirements-dev.txt
pytest
```

---

시스템 내 위치, 웹훅 payload 형식, 알려진 한계는 [docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md)를 참고하세요.
