"""
YouTube 업로드 서비스. (기존 youtube_uploader.py)

OAuth 2.0 토큰(token.json)을 이용해 영상을 업로드합니다.
서버는 대화형 로그인을 할 수 없으므로, 토큰이 없거나 갱신에 실패하면
예외를 던져 운영자가 `scripts/refresh_youtube_token.py` 를 실행하도록 유도합니다.
"""

import json
from pathlib import Path
from typing import List, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# 업로드용 스코프
SCOPES = list(settings.YOUTUBE_SCOPES)

# 재개 가능 업로드 청크 크기 (5MB)
_CHUNK_SIZE = 1024 * 1024 * 5
# YouTube 제목 최대 길이
_MAX_TITLE_LENGTH = 100
# '인물 및 블로그' 카테고리
_CATEGORY_ID = "22"


def get_authenticated_service():
    """
    캐시된 토큰으로 인증된 YouTube API 클라이언트를 만듭니다.

    토큰이 만료되었으면 refresh_token으로 자동 갱신하고 파일에 다시 저장합니다.
    갱신이 불가능하면 예외를 던집니다(서버에서는 대화형 재인증이 불가능하므로).
    """
    token_file: Path = settings.YOUTUBE_TOKEN_FILE
    creds: Optional[Credentials] = None

    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        except Exception as exc:
            logger.error("캐시된 토큰 로드 실패: %s", exc)
            creds = None

    if creds and creds.valid:
        return build("youtube", "v3", credentials=creds)

    # 만료되었지만 refresh_token이 있으면 갱신을 시도합니다.
    if creds and creds.expired and creds.refresh_token:
        logger.info("YouTube 토큰이 만료되어 갱신을 시도합니다...")
        try:
            creds.refresh(Request())
            token_file.write_text(creds.to_json())
            logger.info("토큰 갱신 및 저장 완료: %s", token_file)
            return build("youtube", "v3", credentials=creds)
        except Exception as exc:
            logger.error("토큰 갱신 실패: %s", exc)

    raise RuntimeError(
        "YouTube 인증 토큰이 없거나 만료되었습니다. "
        "터미널에서 'python scripts/refresh_youtube_token.py' 를 실행해 재인증하세요."
    )


def upload_video(
    file_path: str,
    title: str,
    description: str,
    tags: Optional[List[str]] = None,
    privacy_status: str = "unlisted",
) -> str:
    """
    영상을 YouTube에 업로드하고 시청 URL을 반환합니다.

    이 함수는 **동기(blocking)** 이며 파일 크기에 따라 오래 걸립니다.
    코루틴에서는 `run_in_threadpool()` 로 감싸 호출하세요.

    Returns:
        "https://youtube.com/shorts/{videoId}" 형식의 URL.
    """
    if not Path(file_path).exists():
        raise FileNotFoundError(f"업로드할 영상 파일을 찾을 수 없습니다: {file_path}")

    youtube = get_authenticated_service()

    # tags가 None으로 들어와도 API가 깨지지 않도록 방어합니다.
    safe_tags = tags if tags is not None else []
    # 한글 태그가 깨지지 않는지 확인하기 위한 직렬화 로깅
    logger.info("업로드 메타데이터 태그: %s", json.dumps(safe_tags, ensure_ascii=False))

    body = {
        "snippet": {
            "title": title[:_MAX_TITLE_LENGTH],  # YouTube 제목은 100자 제한
            "description": description,
            "tags": safe_tags,
            "categoryId": _CATEGORY_ID,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    # 네트워크가 끊겨도 이어서 올릴 수 있도록 resumable 업로드를 사용합니다.
    media = MediaFileUpload(
        file_path,
        mimetype="video/mp4",
        chunksize=_CHUNK_SIZE,
        resumable=True,
    )

    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    logger.info("YouTube 업로드 시작: %s", file_path)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info("업로드 진행률: %d%%", int(status.progress() * 100))

    video_id = response.get("id")
    if not video_id:
        raise RuntimeError("업로드는 완료되었으나 YouTube가 video ID를 반환하지 않았습니다.")

    logger.info("YouTube 업로드 성공. Video ID: %s", video_id)
    return f"https://youtube.com/shorts/{video_id}"
