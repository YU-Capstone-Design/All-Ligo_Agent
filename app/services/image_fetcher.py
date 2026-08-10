"""
요청으로 전달된 이미지 URL을 내려받아 로컬에 저장하는 모듈.

Spring 백엔드는 이미지 바이너리 대신 S3 URL 목록을 넘겨줍니다.
여러 장을 순차적으로 받으면 느리므로 `asyncio.gather` 로 병렬 다운로드합니다.
"""

import asyncio
from pathlib import Path
from typing import List

import httpx

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

_DOWNLOAD_TIMEOUT_SEC = 10.0

# 응답 Content-Type → 저장할 확장자. 목록에 없으면 .png 로 저장합니다.
_CONTENT_TYPE_EXTENSIONS = [
    ("jpeg", ".jpg"),
    ("jpg", ".jpg"),
    ("webp", ".webp"),
    ("gif", ".gif"),
]
_DEFAULT_EXTENSION = ".png"


class ImageDownloadError(Exception):
    """이미지 URL 다운로드 실패. 라우터에서 400 응답으로 변환합니다."""


def _extension_for(content_type: str) -> str:
    """응답 헤더의 Content-Type으로 저장할 파일 확장자를 결정합니다."""
    for keyword, ext in _CONTENT_TYPE_EXTENSIONS:
        if keyword in content_type:
            return ext
    return _DEFAULT_EXTENSION


async def _download_one(client: httpx.AsyncClient, url: str, task_id: str, index: int) -> Path:
    """이미지 한 장을 내려받아 static/uploads 에 저장하고 경로를 반환합니다."""
    try:
        response = await client.get(url)
        response.raise_for_status()
    except Exception as exc:
        logger.error("[%s] 이미지 다운로드 실패: %s (%s)", task_id, url, exc)
        raise ImageDownloadError(f"이미지 다운로드 실패: {url}") from exc

    ext = _extension_for(response.headers.get("content-type", ""))
    saved_path = settings.UPLOADS_DIR / f"upload_{task_id}_url_{index}{ext}"
    saved_path.write_bytes(response.content)

    return saved_path


async def download_images(urls: List[str], task_id: str) -> List[Path]:
    """
    이미지 URL 목록을 병렬로 내려받습니다.

    한 장이라도 실패하면 `ImageDownloadError` 를 발생시킵니다.
    일부만 성공한 채로 콘텐츠를 만들면 결과 품질을 보장할 수 없기 때문입니다.
    """
    if not urls:
        return []

    settings.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT_SEC) as client:
        tasks = [_download_one(client, url, task_id, idx) for idx, url in enumerate(urls)]
        return list(await asyncio.gather(*tasks))
