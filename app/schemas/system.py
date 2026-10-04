"""시스템 모니터링(GPU·디스크·동시 작업 수) 관련 응답 스키마."""

from typing import List, Optional

from pydantic import BaseModel, Field


class GpuStatus(BaseModel):
    """GPU 상태 정보를 담는 모델입니다. nvidia-smi를 통해 조회됩니다."""

    gpuUtil: int = Field(..., description="GPU 연산 유닛 사용률 (0~100%). 90% 이상이면 과부하 상태입니다.", example=45)
    gpuMemUtil: int = Field(..., description="GPU 메모리 사용률 (0~100%)", example=62)
    gpuMemUsedMb: int = Field(..., description="현재 사용 중인 GPU 메모리 (MB 단위)", example=10240)
    gpuMemTotalMb: int = Field(..., description="GPU 전체 메모리 용량 (MB 단위). RTX 4080 기준 16384MB", example=16384)


class SystemStatusResponse(BaseModel):
    """서버 시스템 상태 응답 모델입니다. Spring 백엔드에서 작업 요청 전 서버 가용 여부를 판단하는 데 사용합니다."""

    status: str = Field(
        ...,
        description="서버 가용 상태. `available`(작업 수락 가능) 또는 `busy`(작업 거부 권장). "
                    "다음 조건 중 하나라도 해당하면 busy: (1) 동시 작업 수 ≥ 최대 허용치, (2) 디스크 여유 ≤ 1GB, (3) GPU 사용률 ≥ 90%",
        example="available",
    )
    activeJobs: int = Field(..., description="현재 백그라운드에서 실행 중인 AI 생성 작업 수", example=0)
    maxConcurrentJobs: int = Field(..., description="서버가 허용하는 최대 동시 작업 수 (현재 고정값: 2)", example=2)
    diskSpaceFreeMb: float = Field(..., description="static/ 디렉토리가 위치한 파티션의 남은 디스크 공간 (MB 단위)", example=51200.50)
    gpu: Optional[GpuStatus] = Field(None, description="GPU 상태 정보. NVIDIA GPU가 없거나 nvidia-smi 실행 실패 시 null")
    timestamp: int = Field(..., description="상태 조회 시점의 Unix 타임스탬프 (초 단위)", example=1716134400)


class PreflightCheck(BaseModel):
    """기동 점검 항목 하나의 결과."""

    name: str = Field(..., description="점검 항목 이름", example="ollama")
    ok: bool = Field(..., description="정상 여부")
    severity: str = Field(..., description="ok | warning(일부 기능 저하) | error(해당 기능 동작 불가)", example="ok")
    detail: str = Field(..., description="상세 설명 또는 조치 방법")


class PreflightResponse(BaseModel):
    """기동 점검 전체 결과."""

    ok: bool = Field(..., description="error 등급 항목이 하나도 없으면 true")
    checks: List[PreflightCheck] = Field(..., description="항목별 점검 결과")
