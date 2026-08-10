# 🚀 Marketing AI Agent (Python Backend)

**로컬 AI 마케팅 에이전트** 프로젝트의 핵심 백엔드 서버(`py/`)입니다.

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
│   │       ├── system.py       # 헬스 체크 (GPU·디스크·작업 수)
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
│   │   ├── webhook_service.py  # Spring 백엔드 결과 통보
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
└── tools/                      # 개발 중 손으로 돌려보는 점검 도구
    ├── check_ollama.py
    └── check_youtube_upload.py
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

### 5. 서버 실행

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

기존 `uvicorn main:app` 명령도 그대로 동작합니다(루트 `main.py`가 앱을 재노출합니다).

서버가 실행되면 `http://localhost:8000/docs` 에서 API 목록을 확인하고 직접 테스트할 수 있습니다.

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
| `POST` | `/api/vision/analyze` | 이미지 → 마케팅 키워드 추출 |
| `GET` | `/` | 서버 접속 확인 페이지 |

## ⚠️ 실행 전 확인사항

- **Ollama 서버**: 텍스트 생성과 이미지 분석을 위해 로컬 `Ollama` 서버가 실행 중이어야 합니다. (`http://localhost:11434`)
  ```bash
  ollama pull gemma4:latest && ollama pull llava:13b
  ```
- **Hugging Face 모델 캐시**: 이미지 생성 모델은 최초 실행 시 자동 다운로드됩니다. (`~/.cache/huggingface/`) 충분한 디스크 공간을 확보해주세요.
- **GPU 사양**: 원활한 AI 모델 구동을 위해 **NVIDIA RTX 4080급(VRAM 16GB 이상) GPU**를 권장합니다. FLUX 로드에 실패하면 자동으로 SDXL로 폴백합니다.
- **FFmpeg NVENC**: GPU 인코딩을 지원하지 않는 FFmpeg 빌드에서는 자동으로 CPU(libx264) 인코딩으로 전환됩니다.

---

더 자세한 프로젝트의 비전, 전체 아키텍처, 향후 계획 등은 [상위 README.md](../README.md)를 참고해주세요.
