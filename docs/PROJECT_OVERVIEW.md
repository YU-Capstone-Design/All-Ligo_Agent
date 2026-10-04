# All-Ligo_Agent — 프로젝트 개요

> 작성일: 2026-10-04 · 기준 커밋: `28d864c` (`origin/main`, 2026-08-18)
> **갱신 2026-10-04**: 같은 날 안정화 커밋(`f416a89`~)으로 §10 의 다수 항목이 해결됨. 작업 내역은 `PLAN.md`·`STATUS.md` 참고.
> 이 문서는 코드를 직접 읽고 작성했습니다. 실행해서 검증한 것은 §9의 "환경 점검"에 별도로 표시했습니다.

## 1. 한 줄 요약

**소상공인용 마케팅 콘텐츠(홍보 문구 / 포스터 이미지 / 9:16 숏폼 영상)를 100% 로컬 AI로 자동 생성하는 FastAPI 서버.**
Spring 백엔드가 요청하면 즉시 `202`를 돌려주고, 백그라운드에서 생성한 뒤 **웹훅**으로 결과를 돌려주며, 확정된 영상은 **YouTube에 업로드**한다.

`All-Ligo` 캡스톤 프로젝트(GitHub 조직 `YU-Capstone-Design`)의 **AI 생성 담당 서버**다.

## 2. 시스템 안에서의 위치

```
 프론트(App/Web) ─► Spring 백엔드 (All-Ligo_Was 로 추정) ──┐
                         ▲   │                               │ POST /api/marketing/generate
        웹훅 콜백         │   │ GET /api/system/status        │ POST /api/marketing/upload
 (content-callback)      │   ▼ (작업 전 가용 여부 확인)        ▼
                    ┌───────────────  All-Ligo_Agent (이 저장소, :8000)  ───────────────┐
                    │  Ollama(gemma4 텍스트 / llava:13b 비전)  ·  FLUX/SDXL 이미지      │
                    │  FFmpeg 영상 · edge-tts 나레이션 · Open-Meteo 날씨                │
                    └──────────────┬───────────────────────────────┬────────────────────┘
                                   ▼                               ▼
                              AWS S3 (영상)                    YouTube (업로드)
```

- 외부 호출자는 **Spring 백엔드**다. 이 서버는 사용자와 직접 통신하지 않는다.
- 결과는 HTTP 응답이 아니라 **웹훅**(`SPRING_WEBHOOK_URL`)으로 전달된다.
- 외부 API 의존은 날씨(Open-Meteo), S3, YouTube뿐이고 **생성 AI는 전부 로컬**이다.

> `All-Ligo_Worker`는 이 서버를 잘라 만든 **별개 저장소**이며 서로 호출하지 않는다. 관계는 최상위 `WORKSPACE.md` 참고.

## 3. 기술 스택

| 영역 | 사용 기술 |
|---|---|
| 웹 | FastAPI, uvicorn, Pydantic, httpx |
| 텍스트 생성 | Ollama + LangChain (`gemma4:latest`, temperature 0.7, `keep_alive=0`) |
| 이미지 분석 | Ollama `/api/chat` 직접 호출 (`llava:13b`, 800×800 리사이즈, `num_ctx=4096`) |
| 이미지 생성 | diffusers — **FLUX.1-schnell 우선, 실패 시 SDXL 폴백** (768×1344) |
| 영상 | FFmpeg(+NVENC→libx264 폴백), PIL 자막, librosa 비트 분석, edge-tts(`ko-KR-SunHiNeural`) |
| 외부 연동 | Open-Meteo(날씨), boto3(S3), google-api-python-client(YouTube) |

## 4. 디렉터리 구조

계층 규칙: `api/routes`는 검증·변환만, `services`가 실제 처리, 환경변수는 `core/config.py`에서만 읽는다.

```
main.py                    # 하위 호환 진입점 (app.main:app 재노출)
app/
  main.py                  # 앱 팩토리: 메타데이터, /static 마운트, 라우터 등록, lifespan
  core/   config.py        # 설정 단일 소스(settings 싱글턴, 경로는 BASE_DIR 기준 절대경로)
          state.py         # JobTracker: 동시 작업 수 카운터 (스레드 안전)
          logging_config.py
  api/    router.py, routes/{marketing,weather,system,vision,home}.py
  schemas/                 # content / youtube / weather / system (Pydantic)
  prompts/marketing.py     # LLM 프롬프트 조립
  services/
    content_pipeline.py    # ★ 생성 파이프라인 (전체 흐름의 중심)
    image_service.py       # FLUX/SDXL 생성 (호출마다 로드→해제)
    vision_service.py      # LLaVA 이미지 분석
    weather_service.py     # Open-Meteo → 프롬프트용 날씨 서술
    image_fetcher.py       # 요청의 이미지 URL 병렬 다운로드
    text_cleaner.py        # LLM 출력 후처리 / [IMAGE_PROMPT] 추출
    storage_service.py     # S3 업로드
    youtube_service.py     # YouTube 업로드 (token.json OAuth, resumable)
    webhook_service.py     # Spring 콜백 전송
    system_service.py      # nvidia-smi / 디스크 / 작업 수 → available|busy
    video/                 # constants, ffmpeg_runner, audio, subtitles, compositor, renderer
  templates/home.html
scripts/  refresh_youtube_token.py, refresh_youtube_token_manual.py   # YouTube OAuth 토큰 발급
tools/    check_ollama.py, check_youtube_upload.py                    # 수동 점검용 (테스트 아님)
```

## 5. API

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/api/marketing/generate` | 콘텐츠 생성 요청. **202 + `taskId`** 즉시 반환, 결과는 웹훅 |
| `POST` | `/api/marketing/upload` | 생성된 mp4를 YouTube에 업로드 (404/400/500) |
| `GET`  | `/api/weather?lat&lon` | 실시간 날씨 + 이미지 프롬프트용 `visualCue` |
| `GET`  | `/api/system/status` | `available`/`busy` 판정 (GPU·디스크·작업 수) |
| `GET`  | `/api/system/preflight` | 기동 점검: 의존성·모델 캐시·YouTube 토큰 (2026-10-04 추가) |
| `POST` | `/api/vision/analyze` | 이미지 → `objects / mood / colors` 키워드 |
| `GET`  | `/` , `/docs` , `/redoc` , `/static/*` | 접속 확인 페이지 / 문서 / 생성물 서빙 |

### 요청 본문 (`/api/marketing/generate`)

camelCase·snake_case를 **둘 다 받는다**(camelCase 우선). 주요 필드: `moodTag`, `hashTag`, `prompt`, `uploadDay`, `uploadTime`, `scheduleId`, `lat/lon`, `contentType`(`POST|VIDEO`, 기본 `POST`), `mode`(`TRANSFORM|ORIGINAL|AUTO`, 기본 `ORIGINAL`), `imageUrls`(최대 5), `topPerformers`(JSON 문자열). 기본값은 `schemas/content.py`의 `resolved_*` 프로퍼티에 모여 있다.

### 웹훅 payload (서버 → Spring)

```jsonc
// 성공
{ "taskId": "...", "scheduleId": "42", "status": "SUCCESS", "jobType": "GENERATE_CONTENT",
  "data": { "contentType": "VIDEO", "mode": "AUTO", "generatedText": "...",
            "posterUrl": "http://host/static/images/poster_*.png",
            "s3VideoUrl": "https://.../content/shortform_*.mp4",
            "localVideoPath": "static/videos/shortform_*.mp4",   // ← /upload 에 그대로 되돌려줌
            "uploadSchedule": "월요일 18:00", "createdAtMillis": 1716134400000 } }
// 실패
{ "taskId": "...", "scheduleId": "42", "status": "FAILED", "jobType": "GENERATE_CONTENT", "error": "..." }
```

## 6. 콘텐츠 생성 파이프라인 (`services/content_pipeline.py`)

1. **mode 해석** — `AUTO`: 업로드 이미지가 있으면 `ORIGINAL`, 없으면 `TRANSFORM`.
2. **이미지 준비** — 업로드 이미지가 있으면 **첫 장만** LLaVA로 분석 → 프롬프트에 삽입.
3. **텍스트 생성** — Ollama(gemma4). 프롬프트에 날씨·분위기·해시태그·요일/시간·(선택)과거 우수 게시물이 들어간다.
4. **포스터 생성** — `VIDEO + TRANSFORM`일 때만. LLM이 낸 `[IMAGE_PROMPT_n]` 최대 3개로 FLUX/SDXL 생성.
5. **텍스트 정리** — 메타 접두사·`#해시태그`·이미지 프롬프트 제거.
6. **영상 렌더링 + S3** — `VIDEO`일 때만.
7. **웹훅 전송**. 어떤 단계든 예외 → `FAILED` 웹훅. 업로드 임시 파일은 `finally`에서 삭제.

### contentType × mode 결과 표

| contentType | mode | 텍스트 | 이미지 | 영상 |
|---|---|---|---|---|
| POST | ORIGINAL / AUTO(이미지 있음) | 3문단+, 이모지 | 업로드 **첫 장**을 포스터로 | — |
| POST | TRANSFORM / AUTO(이미지 없음) | 3문단+, 이모지 | **없음** (`posterUrl=null`) | — |
| VIDEO | ORIGINAL / AUTO(이미지 있음) | 50자 이내 1~2문장 | 업로드 이미지 **전부** 사용 | 생성 |
| VIDEO | TRANSFORM / AUTO(이미지 없음) | 50자 이내 + 영어 프롬프트 3개 | AI 3장 생성 (업로드는 분석에만 사용) | 생성 |
| VIDEO | ORIGINAL + 이미지 없음 | 생성 | 없음 | **건너뜀** (`SUCCESS`인데 영상 필드 `null`) |

### 영상 렌더링 (`services/video/`)

- 출력 1080×1920, 30fps. 이미지 9:16 크롭 → **TTS 길이(+1.5초 여운)가 영상 길이의 기준**(TTS 실패 시 장수×3초).
- `static/bgm/*.mp3`에서 무작위 선택(없으면 BGM 없음) → librosa 비트 분석으로 컷 타이밍 결정.
- 이미지별 Ken Burns 프리셋 5종 순환, xfade 전환 4종(`hblur, zoomin, fade, circlecrop`) 순환.
- PIL로 그린 타이핑 자막 오버레이(Pretendard ExtraBold, 없으면 Noto Sans CJK). NVENC 실패 시 libx264로 자동 재시도.

## 7. 설정 (`app/core/config.py`)

`.env` 또는 환경변수. 모든 값에 기본값이 있다.

| 변수 | 기본값 | 용도 |
|---|---|---|
| `SPRING_WEBHOOK_URL` | `http://localhost:8080/api/internal/content-callback` | 결과 웹훅 대상 |
| `OLLAMA_BASE_URL` / `OLLAMA_TEXT_MODEL` / `OLLAMA_VISION_MODEL` | `localhost:11434` / `gemma4:latest` / `llava:13b` | 로컬 LLM |
| `MAX_CONCURRENT_JOBS` | `2` | busy 판정 임계값 (**강제 아님**, §10-4) |
| `AWS_S3_BUCKET` / `AWS_REGION` / `AWS_S3_BASE_URL` | – / `ap-northeast-2` / – | 영상 S3 업로드 (+ boto3 표준 `AWS_ACCESS_KEY_ID/SECRET`) |
| `WEBHOOK_TIMEOUT_SEC` / `WEATHER_TIMEOUT_SEC` / `OLLAMA_VISION_TIMEOUT_SEC` | `5` / `5` / `90` | 타임아웃 (비전은 10/4 에 15→90) |
| `OLLAMA_TEXT_TIMEOUT_SEC` / `OLLAMA_TEXT_THINKING` / `OLLAMA_TEXT_MAX_TOKENS` | `300` / `false` / `2048` | 텍스트 생성 안정화 (10/4 추가) |
| `IMAGE_MODEL_ALLOW_DOWNLOAD` | `false` | 캐시에 없는 이미지 모델 다운로드 허용 (10/4 추가) |

고정 임계값: 디스크 여유 ≤ 1000MB, GPU 사용률 ≥ 90%, GPU 메모리 사용률 ≥ 80% → `busy`.
시크릿 파일(`.env`, `client_secret.json`, `token.json`)은 `.gitignore` 대상이며 **git 이력에 커밋된 적 없음**(확인함).

## 8. 실행 방법

```bash
sudo apt install -y ffmpeg fonts-noto-cjk
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt   # torch는 CUDA 빌드에 맞게 별도 설치
ollama pull gemma4:latest && ollama pull llava:13b
mkdir -p fonts static/bgm                       # fonts/Pretendard-ExtraBold.otf 배치, bgm은 선택
python scripts/refresh_youtube_token.py         # YouTube 업로드를 쓸 때만 (client_secret.json 필요)
uvicorn app.main:app --host 0.0.0.0 --port 8000 # 반드시 이 저장소 루트에서 실행 (구 `uvicorn main:app` 도 동작)
```

> 최상위 `server.log`의 `Could not import module "main"` 오류는 **저장소 루트가 아닌 상위 폴더에서 uvicorn을 실행**해서 생긴 것으로 보인다.

## 9. 개발 이력 요약 (커밋 기준, 최근 30개 범위)

| 시기 | 내용 |
|---|---|
| 2026-05-11 | 날씨 API, 음성(TTS) 삽입 |
| 2026-05-19~20 | 비동기 처리, `/api/system/status`, LLaVA 이미지 분석, 우수 게시물 파라미터, YouTube 자동 업로드, 타이핑 자막 |
| 2026-05-30~06-01 | Spring API 동기화, S3 업로드, 이미지 URL 수신 방식(S3→저장 후 사용), 15초 타임아웃 |
| 2026-05-31 | 텍스트 모델 `qwen2.5:3b → gemma4`, 파이프라인 분기(POST/VIDEO·모드), Ollama VRAM 누수 수정 |
| 2026-06-03 | 영상 길이 문제 수정 시도, 제목 해시태그 제거 |
| 2026-06-20~22 | YouTube refresh token 스크립트, LLaVA 13B 업그레이드, 이미지 생성 성능 최적화 1차 |
| 2026-08-11 | **FastAPI 표준 계층 구조로 리팩터링** (모놀리스 `main.py` 1,050줄 → `app/` 패키지, `print` → `logging`) |
| 2026-08-18 | `.gitignore`에 `.idea` 추가 (마지막 커밋) |

## 10. 알려진 한계 / 주의점 (코드 기준)

> ✅ = 2026-10-04 안정화 작업에서 해결. 나머지는 Spring 과 합의가 필요하거나 미착수.

1. **인증 없음** — 모든 엔드포인트와 `/static`(생성 이미지·영상)이 무인증으로 공개된다. 내부망/터널 뒤 운용을 전제한 구조.
2. ✅ (`6f4529a` static/videos 하위만 허용) **`/api/marketing/upload` 경로 검증이 약함** — `localVideoPath`가 절대경로면 그대로 쓰이고(`settings.resolve_path`), 검사는 "존재 + `.mp4` + 0바이트 아님"뿐이라 `static/` 밖의 mp4도 업로드될 수 있다. 외부 노출 시 `static/videos` 하위로 제한 필요.
3. **`privacyStatus` 무시** — 요청 값과 관계없이 항상 `unlisted`로 업로드(코드 주석에 명시된 의도적 고정).
4. (일부 해결 `eb6634d`: 이미지 생성 구간은 GPU 잠금으로 직렬화 → OOM 방지. 요청 거절/큐잉 정책은 Spring 합의 필요) **동시 작업 제한은 권고일 뿐** — `generate`는 `busy`여도 거절하지 않는다. `job_tracker`는 카운트만 하고, 호출자(Spring)가 `/api/system/status`를 먼저 확인해야 한다.
5. ✅ (`eb6634d` 작업당 1회 로드, 캐시 전용) **이미지 모델을 장마다 로드/해제** — `generate_image()`가 호출될 때마다 FLUX를 로드하고 끝나면 해제(VRAM 0 보장 목적). `VIDEO+TRANSFORM`은 3번 반복되어 느리다. (코드 구조상의 추정이며 실제 소요 시간은 측정하지 않았다.)
5-1. ✅ (`f416a89` uuid) 파일명이 `int(time.time())` 기반(`poster_*`, `shortform_*`, `_tmp_*`)이라 **같은 초에 시작한 동시 작업끼리 충돌**할 수 있다.
6. (일부 해결 `5e501fd`: 2xx 확인 + 실패 payload 보관 + `tools/resend_failed_webhooks.py`. 자동 재시도는 Spring 중복 처리 합의 필요) **웹훅은 1회 시도, 재시도 없음** — 실패 시 로그만 남기므로 Spring이 못 받으면 결과를 잃는다. (영상은 디스크에 남음)
7. **웹훅의 `mode`는 요청 원본 값**(`AUTO` 포함)이다. 스키마 설명의 "TRANSFORM 또는 ORIGINAL"과 다르다.
8. ✅ (`9af89ca` pytest 스모크 24개) **테스트 코드 없음.** `tools/`는 수동 점검 스크립트다.
9. ✅ (README 갱신) **README 일부가 낡음** — Agent `README.md`는 "RTX 4080"을 전제하고 헤더에 `py/`로 적혀 있다. 최상위 `README.md`는 구버전(§`WORKSPACE.md`).

## 11. 환경 점검 결과 (2026-10-04, 이 머신에서 직접 확인)

- 저장소: `main` = `origin/main` = `28d864c`, 작업 트리 깨끗 (오늘 `6437aff → 28d864c` fast-forward pull 수행).
- 가상환경은 **상위 폴더** `../venv`(Python 3.12.3). fastapi 0.136.1, torch 2.11.0, diffusers 0.37.1, transformers 5.7.0, langchain 1.2.15, edge-tts 7.2.8, librosa 0.11.0, boto3 1.43.18 설치됨.
- 시스템: `ffmpeg`/`ffprobe`/`ollama`/`nvidia-smi` 존재. **GPU는 RTX 4090 (24GB)** — 문서의 4080(16GB)과 다름.
- Ollama가 `127.0.0.1:11434`에서 실행 중이며 `llava:13b`, `gemma4:latest`(그 외 `gemma4:26b`, `llama3:8b`) 설치됨.
- `fonts/`에 Pretendard 있음, `static/bgm`·`static/images`·`static/videos` 존재.
- **Agent 서버(8000)는 현재 떠 있지 않음.** 서버를 기동해 엔드포인트를 호출해 보는 검증은 하지 않았다.
- 아직 확인하지 않은 것: `.env`의 값들(키 이름만 확인), YouTube `token.json`의 유효성(마지막 수정 2026-06-24, 만료 가능성 높음), S3/Spring 연결, FLUX 모델 캐시 존재 여부.

### 10-추가. 2026-10-04 안정화 중 새로 발견해 해결한 문제

| 문제 | 영향 | 커밋 |
|---|---|---|
| LLM·웹훅·날씨 호출이 이벤트 루프를 막음 | 생성 중 `/api/system/status` 등 모든 요청이 멈춤 | `e04636f` |
| gemma4 thinking 모드 기본 켜짐 | POST 텍스트 생성이 10초~3분 18초 → 끄고 4~6초 | `e04636f` |
| topPerformers/LLaVA 결과의 `{}` 가 템플릿을 깨뜨림 | 해당 요청 FAILED | `804a40d` |
| FLUX 가 캐시에 없어 매번 다운로드 시도 | 첫 시연 때 지연/실패 위험 | `eb6634d` |
| LLaVA 15초 타임아웃 < 첫 로드 39초, 응답 첫 줄 공백으로 객체 키워드 파싱 실패 | 이미지 분석이 사실상 항상 무효 | `734841c` |
| 이미지 URL 검증 없음 (HTML 에러 페이지도 저장) | 영상 단계에서 조용히 실패 | `269c968` |
| `edge-tts` 를 PATH 에서 찾음 | venv 미활성 실행 시 나레이션 누락 | `97e4882` |
| 자막 폰트에 이모지 없음 | 영상에 네모(□) 출력 | `c67218a` |
