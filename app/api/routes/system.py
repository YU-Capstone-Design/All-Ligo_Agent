"""시스템 모니터링(헬스 체크) 라우터."""

from fastapi import APIRouter

from app.schemas.system import SystemStatusResponse
from app.services import system_service

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
