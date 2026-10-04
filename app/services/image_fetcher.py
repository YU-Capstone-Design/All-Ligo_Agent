"""
요청으로 전달된 이미지 URL을 내려받아 로컬에 저장하는 모듈.

Spring 백엔드는 이미지 바이너리 대신 S3 URL 목록을 넘겨줍니다.
여러 장을 순차적으로 받으면 느리므로 `asyncio.gather` 로 병렬 다운로드합니다.

받은 데이터는 저장 전에 검증합니다(크기 상한, 실제 이미지인지). 이미지가 아닌 응답
(S3 에러 페이지 등)을 그대로 저장하면 한참 뒤 영상 렌더링 단계에서야 실패해서
원인을 찾기 어렵기 때문에, 요청 단계에서 400으로 바로 돌려보냅니다.
"""

import asyncio
from io import BytesIO
from pathlib import Path
from typing import List
from urllib.parse import urlparse

import httpx
from PIL import Image

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

_DOWNLOAD_TIMEOUT_SEC = 10.0

# 이미지 한 장의 최대 크기. 스마트폰 원본 사진도 보통 10MB 안쪽입니다.
_MAX_IMAGE_BYTES = 20 * 1024 * 1024

_ALLOWED_SCHEMES = ("http", "https")

# PIL 이 판별한 실제 포맷 → 저장할 확장자. 목록에 없으면 .png 로 저장합니다.
# (S3 는 Content-Type 을 application/octet-stream 으로 주는 경우가 많아 헤더 대신 내용으로 판별)
_FORMAT_EXTENSIONS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "GIF": ".gif",
}
_DEFAULT_EXTENSION = ".png"


class ImageDownloadError(Exception):
    """이미지 URL 다운로드 실패. 라우터에서 400 응답으로 변환합니다."""


def _validate_url(url: str) -> None:
    """http/https 가 아닌 URL(file://, ftp:// 등)은 거부합니다."""
    if urlparse(url).scheme.lower() not in _ALLOWED_SCHEMES:
        raise ImageDownloadError(f"http/https 이미지 URL만 지원합니다: {url}")


def _detect_extension(data: bytes, url: str) -> str:
    """
    내려받은 바이트가 실제 이미지인지 확인하고 저장할 확장자를 반환합니다.

    PIL 이 열 수 없는 데이터(HTML 에러 페이지, HEIC 등 미지원 포맷)면 예외를 던집니다.
    """
    try:
        with Image.open(BytesIO(data)) as img:
            image_format = img.format
            img.verify()  # 잘린 파일 등 손상 여부 검사
    except Exception as exc:
        raise ImageDownloadError(
            f"이미지로 읽을 수 없는 파일입니다(JPG/PNG/WebP/GIF 지원): {url}"
        ) from exc

    return _FORMAT_EXTENSIONS.get(image_format or "", _DEFAULT_EXTENSION)


async def _fetch_bytes(client: httpx.AsyncClient, url: str) -> bytes:
    """
    URL 내용을 크기 상한을 지키며 내려받습니다.

    응답을 스트리밍으로 읽어서, 상한을 넘는 순간 중단합니다.
    (전체를 메모리에 받은 뒤 검사하면 거대한 파일 하나로 메모리가 고갈될 수 있음)
    """
    async with client.stream("GET", url) as response:
        response.raise_for_status()

        chunks: List[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > _MAX_IMAGE_BYTES:
                raise ImageDownloadError(
                    f"이미지가 너무 큽니다(최대 {_MAX_IMAGE_BYTES // (1024 * 1024)}MB): {url}"
                )
            chunks.append(chunk)
        return b"".join(chunks)


async def _download_one(client: httpx.AsyncClient, url: str, task_id: str, index: int) -> Path:
    """이미지 한 장을 내려받아 검증한 뒤 static/uploads 에 저장하고 경로를 반환합니다."""
    _validate_url(url)

    try:
        data = await _fetch_bytes(client, url)
    except ImageDownloadError:
        raise
    except Exception as exc:
        logger.error("[%s] 이미지 다운로드 실패: %s (%s)", task_id, url, exc)
        raise ImageDownloadError(f"이미지 다운로드 실패: {url}") from exc

    ext = _detect_extension(data, url)
    saved_path = settings.UPLOADS_DIR / f"upload_{task_id}_url_{index}{ext}"
    saved_path.write_bytes(data)

    return saved_path


async def download_images(urls: List[str], task_id: str) -> List[Path]:
    """
    이미지 URL 목록을 병렬로 내려받습니다.

    한 장이라도 실패하면 `ImageDownloadError` 를 발생시킵니다.
    일부만 성공한 채로 콘텐츠를 만들면 결과 품질을 보장할 수 없기 때문입니다.
    이 경우 이미 저장된 다른 이미지들은 지웁니다(작업이 시작되지 않으므로 아무도 정리하지 않음).
    """
    if not urls:
        return []

    settings.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    # CDN/S3 앞단의 리다이렉트(301/302)도 따라갑니다.
    async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT_SEC, follow_redirects=True) as client:
        tasks = [_download_one(client, url, task_id, idx) for idx, url in enumerate(urls)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    errors = [r for r in results if isinstance(r, BaseException)]
    if errors:
        for saved in results:
            if isinstance(saved, Path):
                saved.unlink(missing_ok=True)
        first = errors[0]
        if isinstance(first, ImageDownloadError):
            raise first
        raise ImageDownloadError(f"이미지 다운로드 실패: {first}") from first

    return list(results)
