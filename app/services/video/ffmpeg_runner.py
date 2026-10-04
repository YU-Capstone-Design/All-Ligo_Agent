"""
FFmpeg 실행 래퍼.

NVENC(GPU 인코딩)가 없는 환경에서도 동작하도록, NVENC 실패 시 자동으로
CPU 인코더(libx264)로 재시도하는 폴백 로직을 담고 있습니다.
"""

import subprocess
from typing import List

from app.core.logging_config import get_logger

logger = get_logger(__name__)


def _to_cpu_fallback_cmd(cmd: List[str]) -> List[str]:
    """
    NVENC용 FFmpeg 명령을 CPU(libx264)용으로 변환합니다.

    치환 규칙
      - "h264_nvenc" → "libx264"
      - "-cq"        → "-crf"        (NVENC의 품질 옵션 ↔ x264의 품질 옵션)
      - "-preset p6" → "-preset fast" (p6는 NVENC 전용 프리셋명이라 x264가 인식 못 함)
    """
    fallback: List[str] = []
    skip_next = False

    for arg in cmd:
        if skip_next:
            # 직전에 "-preset fast"를 넣었으므로 원래의 프리셋 값(p6)은 건너뜁니다.
            skip_next = False
            continue

        if arg == "h264_nvenc":
            fallback.append("libx264")
        elif arg == "-cq":
            fallback.append("-crf")
        elif arg == "-preset":
            fallback.extend(["-preset", "fast"])
            skip_next = True
        else:
            fallback.append(arg)

    return fallback


def run_ffmpeg(cmd: List[str], timeout: int = 120) -> None:
    """
    FFmpeg 명령을 실행합니다.

    NVENC가 포함된 명령이 실패하면 CPU 인코더로 한 번 더 시도합니다.
    그 외의 실패는 예외를 그대로 전파합니다.
    """
    try:
        logger.debug("FFmpeg 실행: %s", " ".join(cmd))
        subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True)
    except subprocess.CalledProcessError as exc:
        if "h264_nvenc" not in cmd:
            logger.error("FFmpeg stdout: %s", exc.stdout)
            logger.error("FFmpeg stderr: %s", exc.stderr)
            raise

        logger.warning("NVENC 인코딩 실패(해당 FFmpeg 빌드가 미지원일 수 있음). CPU(libx264)로 폴백합니다.")
        fallback_cmd = _to_cpu_fallback_cmd(cmd)
        logger.debug("FFmpeg 폴백 실행: %s", " ".join(fallback_cmd))
        try:
            subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=timeout, check=True)
        except subprocess.CalledProcessError as fallback_exc:
            # 폴백까지 실패하면 원인을 알 수 있도록 FFmpeg 의 마지막 출력을 남깁니다.
            logger.error("FFmpeg(CPU 폴백) stderr: %s", (fallback_exc.stderr or "")[-2000:])
            raise


def probe_duration(media_path: str) -> float:
    """ffprobe로 미디어 파일의 재생 시간(초)을 조회합니다."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(media_path),
    ]
    return float(subprocess.check_output(cmd).decode("utf-8").strip())
