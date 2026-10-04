"""
YouTube 업로드 서비스. (기존 youtube_uploader.py)

OAuth 2.0 토큰(token.json)을 이용해 영상을 업로드합니다.
서버는 대화형 로그인을 할 수 없으므로, 토큰이 없거나 갱신에 실패하면
예외를 던져 운영자가 `scripts/refresh_youtube_token.py` 를 실행하도록 유도합니다.
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

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


# ---------------------------------------------------------------------------
# 중복 업로드 방지
# ---------------------------------------------------------------------------
# Was 는 업로드 요청이 실패(5xx·타임아웃)하면 다음 분에 다시 요청합니다. Agent 가 업로드를
# 끝냈는데 응답만 유실된 경우 그대로 다시 올리면 YouTube 에 같은 영상이 두 개 생깁니다.
# 그래서 영상 파일(절대 경로) 기준으로 업로드 결과를 기록해 두고, 같은 영상이면 기존 URL 을
# 돌려줍니다. 같은 영상의 업로드가 진행 중일 때 재요청이 오면 앞 업로드가 끝날 때까지 기다립니다.

_path_locks: Dict[str, threading.Lock] = {}
_path_locks_guard = threading.Lock()
_records_lock = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _path_locks_guard:
        return _path_locks.setdefault(key, threading.Lock())


def _load_records() -> dict:
    try:
        return json.loads(settings.YOUTUBE_UPLOADS_FILE.read_text())
    except FileNotFoundError:
        return {}
    except Exception as exc:
        # 기록 파일이 깨졌어도 업로드 자체는 막지 않습니다.
        logger.error("업로드 기록 파일을 읽지 못했습니다(무시): %s", exc)
        return {}


def _save_record(key: str, youtube_url: str) -> None:
    with _records_lock:
        records = _load_records()
        records[key] = {"youtubeUrl": youtube_url, "uploadedAt": datetime.now().isoformat(timespec="seconds")}
        # 쓰는 도중 서버가 죽어도 기록이 깨지지 않도록 임시 파일에 쓴 뒤 바꿔치기합니다.
        tmp = settings.YOUTUBE_UPLOADS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(records, indent=2, ensure_ascii=False))
        os.replace(tmp, settings.YOUTUBE_UPLOADS_FILE)


def find_uploaded_url(file_path: str) -> Optional[str]:
    """이미 업로드한 영상이면 그 YouTube URL 을, 아니면 None 을 반환합니다."""
    key = str(Path(file_path).resolve())
    with _records_lock:
        record = _load_records().get(key)
    return record["youtubeUrl"] if record else None


def upload_video_once(
    file_path: str,
    title: str,
    description: str,
    tags: Optional[List[str]] = None,
) -> str:
    """
    `upload_video()` 의 중복 방지 버전. 같은 영상 파일은 한 번만 YouTube 에 올립니다.

    Returns:
        YouTube URL. 이미 올린 영상이면 처음 올렸을 때의 URL.
    """
    key = str(Path(file_path).resolve())

    with _lock_for(key):
        existing = find_uploaded_url(key)
        if existing:
            logger.info("이미 업로드된 영상입니다. 기존 URL 을 돌려줍니다: %s → %s", key, existing)
            return existing

        youtube_url = upload_video(file_path, title, description, tags)
        try:
            _save_record(key, youtube_url)
        except Exception as exc:
            # 기록 실패는 업로드 결과에 영향을 주지 않지만, 재요청 시 중복 업로드될 수 있습니다.
            logger.error("업로드 기록 저장 실패(중복 방지가 안 될 수 있음): %s", exc)
        return youtube_url
