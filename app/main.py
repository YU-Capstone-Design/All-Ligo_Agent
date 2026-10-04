"""
FastAPI 애플리케이션 진입점.

이 파일은 "앱을 조립하는 일"만 합니다.
    - 메타데이터(제목/설명/태그) 정의
    - 로깅 초기화, 런타임 디렉터리 생성
    - 정적 파일 마운트
    - 라우터 등록

비즈니스 로직은 절대 여기에 두지 마세요. 라우팅은 `app/api/`,
실제 처리는 `app/services/` 에 위치합니다.

실행:
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import settings
from app.core.logging_config import get_logger, setup_logging
from app.services import preflight_service

logger = get_logger(__name__)

API_DESCRIPTION = """
## 소상공인을 위한 AI 마케팅 콘텐츠 자동 생성 서버

이 API는 **100% 로컬 AI 모델**을 활용하여 소상공인의 마케팅 콘텐츠(텍스트, 포스터 이미지, 숏폼 영상)를 자동으로 생성합니다.

### 🏗️ 시스템 아키텍처
- **텍스트 생성**: Ollama + Gemma4:latest (로컬 LLM)
- **이미지 생성**: FLUX.1-schnell / Stable Diffusion XL (로컬 GPU)
- **영상 생성**: FFmpeg 기반 Ken Burns 효과 + 트랜지션
- **이미지 분석**: LLaVA Vision-LLM (로컬)
- **날씨 연동**: Open-Meteo API (외부)

### 🔄 비동기 처리 흐름
1. Spring 백엔드가 콘텐츠 생성을 요청합니다.
2. 이 서버는 즉시 `202 Accepted`와 `taskId`를 반환합니다.
3. 백그라운드에서 AI 생성 작업이 진행됩니다.
4. 작업 완료 시, Spring 백엔드의 웹훅 URL로 결과를 전송합니다.

### ⚙️ 환경 변수
- `SPRING_WEBHOOK_URL`: 작업 완료 시 결과를 전송할 Spring 백엔드 콜백 URL (기본값: `http://localhost:8080/api/internal/content-callback`)
- `OLLAMA_BASE_URL`: Ollama 서버 주소 (기본값: `http://localhost:11434`)
- `MAX_CONCURRENT_JOBS`: 최대 동시 작업 수 (기본값: 2)
- `AWS_S3_BUCKET` / `AWS_REGION` / `AWS_S3_BASE_URL`: 영상 S3 업로드 설정
"""

# OpenAPI 문서에서 엔드포인트를 묶어 보여줄 태그 정의
OPENAPI_TAGS = [
    {
        "name": "🖼️ 마케팅 콘텐츠 생성",
        "description": "AI를 활용한 마케팅 텍스트, 포스터 이미지, 숏폼 영상을 비동기로 생성합니다. 요청 즉시 `taskId`를 반환하며, 완료 시 웹훅으로 결과를 전송합니다.",
    },
    {
        "name": "🌤️ 날씨 정보",
        "description": "Open-Meteo API를 통해 실시간 날씨 정보를 조회합니다. 마케팅 콘텐츠 생성 시 날씨 분위기를 반영하는 데 사용됩니다.",
    },
    {
        "name": "🖥️ 시스템 모니터링",
        "description": "서버의 현재 상태(GPU, 디스크, 동시 작업 수)를 조회하여 작업 가능 여부를 확인합니다.",
    },
    {
        "name": "👁️ 이미지 분석 (Vision)",
        "description": "Vision-LLM(LLaVA)을 사용하여 업로드된 이미지에서 마케팅 키워드(객체, 분위기, 색감)를 추출합니다.",
    },
    {
        "name": "🏠 홈",
        "description": "서버 접속 확인용 테스트 페이지입니다.",
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    애플리케이션 시작/종료 훅.

    기존에는 모듈 임포트 시점에 `os.makedirs()` 를 호출했는데, 임포트만 해도
    부작용이 생겨 테스트가 어려웠습니다. 디렉터리 준비는 여기로 옮겼습니다.
    """
    setup_logging()

    for directory in settings.RUNTIME_DIRS:
        directory.mkdir(parents=True, exist_ok=True)

    logger.info("서버 시작 — 프로젝트 루트: %s", settings.BASE_DIR)
    logger.info("웹훅 대상: %s", settings.SPRING_WEBHOOK_URL)
    logger.info("Ollama: %s (텍스트=%s, 비전=%s)",
                settings.OLLAMA_BASE_URL, settings.OLLAMA_TEXT_MODEL, settings.OLLAMA_VISION_MODEL)

    # 의존성 점검은 네트워크 호출이 있어 몇 초 걸리므로, 기동을 막지 않도록 백그라운드에서 돌립니다.
    threading.Thread(target=preflight_service.log_preflight, name="preflight", daemon=True).start()
    # YouTube 토큰은 업로드 요청이 와야 죽은 걸 알 수 있으므로 주기적으로 점검합니다.
    preflight_service.start_youtube_token_monitor()

    yield

    logger.info("서버 종료.")


def create_app() -> FastAPI:
    """
    FastAPI 앱 인스턴스를 만들어 반환합니다.

    팩토리 패턴으로 두면 테스트에서 설정을 바꿔가며 앱을 새로 만들 수 있습니다.
    """
    application = FastAPI(
        title="📢 All-Ligo 마케팅 AI 에이전트 API",
        description=API_DESCRIPTION,
        version="2.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )

    # 정적 파일 서빙 전에 디렉터리가 존재해야 하므로 여기서도 한 번 보장합니다.
    # (lifespan은 앱 생성 이후에 실행되기 때문입니다.)
    for directory in settings.RUNTIME_DIRS:
        directory.mkdir(parents=True, exist_ok=True)

    application.mount("/static", StaticFiles(directory=settings.STATIC_DIR), name="static")
    application.include_router(api_router)

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
