"""시스템 모니터링(헬스 체크) 라우터."""

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

from app.schemas.system import PreflightResponse, SystemStatusResponse
from app.services import preflight_service, system_service

router = APIRouter(tags=["🖥️ 시스템 모니터링"])


@router.get(
    "/api/system/status",
    response_model=SystemStatusResponse,
    summary="서버 시스템 상태 조회 (Health Check)",
    description="""
서버의 현재 상태를 조회합니다. Spring 백엔드에서 **무거운 AI 생성 작업을 요청하기 전에** 이 API를 호출하여 서버가 작업을 수락할 수 있는 상태인지 확인해야 합니다.

### 반환되는 상태값
| status | 의미 | 권장 행동 |
|--------|------|--------|
| `available` | 서버가 새 작업을 수락할 수 있음 | 콘텐츠 생성 요청 가능 |
| `busy` | 서버가 과부하 상태 | 요청을 잠시 뒤로 미루기 |

### busy 판정 기준 (하나라도 해당 시)
1. 현재 실행 중인 작업 수 ≥ 최대 동시 작업 수 (2개)
2. 디스크 여유 공간 ≤ 1,000MB
3. GPU 사용률 ≥ 90%

### 파라미터
이 API는 파라미터가 없습니다. 호출하면 즉시 현재 상태를 반환합니다.
""",
    responses={200: {"description": "시스템 상태 조회 성공"}},
)
async def get_system_status() -> SystemStatusResponse:
    return system_service.get_system_status()


@router.get(
    "/api/system/preflight",
    response_model=PreflightResponse,
    summary="기동 점검 (의존성·모델·토큰 상태)",
    description="""
콘텐츠 생성에 필요한 외부 요소가 준비되어 있는지 점검합니다. 서버 기동 시에도 한 번 실행되어 로그에 요약됩니다.

| 항목 | 내용 |
|------|------|
| ffmpeg / ffprobe | 영상 생성 |
| edge-tts | 나레이션 |
| ollama | 서버 연결, 텍스트·비전 모델 설치 여부 |
| 이미지 모델 | FLUX / SDXL 로컬 캐시 여부 |
| 자막 폰트, BGM, 디스크 | 영상 품질 / 저장 공간 |
| S3 | 버킷·자격 증명 설정 여부 (업로드 권한은 확인하지 않음) |
| YouTube 토큰 | refresh token 유효성 (파일은 수정하지 않음) |

`severity`: `ok` / `warning`(일부 기능 저하) / `error`(해당 기능 동작 불가).
몇 초 걸릴 수 있습니다(Ollama, Google 토큰 서버 호출).
""",
)
async def get_preflight() -> PreflightResponse:
    # Ollama·Google 네트워크 호출이 있어 스레드풀에서 실행합니다.
    return await run_in_threadpool(preflight_service.run_preflight)
