"""
서버 하드웨어 리소스(GPU·디스크) 조회 및 가용 여부 판정 서비스.

Spring 백엔드가 무거운 AI 작업을 요청하기 전에 이 정보를 확인해서
서버가 과부하 상태인지 판단합니다.
"""

import shutil
import subprocess
import time
from typing import Optional

from app.core.config import settings
from app.core.logging_config import get_logger
from app.core.state import job_tracker
from app.schemas.system import GpuStatus, SystemStatusResponse

logger = get_logger(__name__)

# nvidia-smi 로 조회할 항목. CSV(헤더/단위 없음)로 받아 파싱합니다.
_NVIDIA_SMI_CMD = [
    "nvidia-smi",
    "--query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total",
    "--format=csv,noheader,nounits",
]
_NVIDIA_SMI_TIMEOUT_SEC = 2


def get_gpu_status() -> Optional[GpuStatus]:
    """
    nvidia-smi로 첫 번째 GPU의 사용률·메모리 정보를 조회합니다.

    NVIDIA GPU가 없거나(예: macOS 개발 환경) nvidia-smi 실행이 실패하면
    예외를 삼키고 None을 반환합니다. GPU 정보는 선택 항목이므로
    헬스 체크 자체가 실패해서는 안 됩니다.
    """
    try:
        result = subprocess.run(
            _NVIDIA_SMI_CMD,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=_NVIDIA_SMI_TIMEOUT_SEC,
        )
        if result.returncode != 0:
            return None

        lines = result.stdout.strip().split("\n")
        if not lines:
            return None

        # 예: "45, 62, 10240, 16384"
        parts = [p.strip() for p in lines[0].split(",")]
        if len(parts) < 4:
            return None

        return GpuStatus(
            gpuUtil=int(parts[0]),
            gpuMemUtil=int(parts[1]),
            gpuMemUsedMb=int(parts[2]),
            gpuMemTotalMb=int(parts[3]),
        )
    except Exception as exc:
        logger.debug("GPU 상태 조회 실패(무시하고 진행): %s", exc)
        return None


def get_free_disk_mb() -> float:
    """static/ 디렉터리가 위치한 파티션의 남은 용량을 MB 단위로 반환합니다."""
    usage = shutil.disk_usage(settings.STATIC_DIR)
    return usage.free / (1024 * 1024)


def _is_gpu_busy(gpu: Optional[GpuStatus]) -> bool:
    """GPU 사용률 또는 메모리 사용률이 임계값을 넘었는지 판정합니다."""
    if gpu is None:
        return False
    return (
        gpu.gpuUtil >= settings.GPU_BUSY_UTIL_PERCENT
        or gpu.gpuMemUtil >= settings.GPU_BUSY_MEM_PERCENT
    )


def get_system_status() -> SystemStatusResponse:
    """
    서버가 새 작업을 받을 수 있는 상태인지 종합 판정합니다.

    다음 중 하나라도 해당하면 `busy` 입니다.
      1. 실행 중인 작업 수 ≥ 최대 동시 작업 수
      2. 디스크 여유 공간 ≤ MIN_FREE_DISK_MB
      3. GPU 사용률 또는 GPU 메모리 사용률이 임계값 초과
    """
    active_jobs = job_tracker.active_count
    free_mb = get_free_disk_mb()
    gpu_status = get_gpu_status()

    is_busy = (
        active_jobs >= settings.MAX_CONCURRENT_JOBS
        or free_mb <= settings.MIN_FREE_DISK_MB
        or _is_gpu_busy(gpu_status)
    )

    return SystemStatusResponse(
        status="busy" if is_busy else "available",
        activeJobs=active_jobs,
        maxConcurrentJobs=settings.MAX_CONCURRENT_JOBS,
        diskSpaceFreeMb=round(free_mb, 2),
        gpu=gpu_status,
        timestamp=int(time.time()),
    )
