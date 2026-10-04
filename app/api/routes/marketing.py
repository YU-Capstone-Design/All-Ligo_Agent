"""
마케팅 콘텐츠 생성 및 YouTube 업로드 라우터.

두 엔드포인트가 한 파일에 있는 이유: Spring 백엔드 입장에서
"콘텐츠를 만든다 → 확정되면 올린다" 는 하나의 흐름이기 때문입니다.
"""

import json
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.logging_config import get_logger
from app.prompts import marketing as marketing_prompts
from app.schemas.content import GenerateRequestDto, JobAcceptedResponse
from app.schemas.youtube import UploadRequest, UploadResponse
from app.services import image_fetcher, weather_service, youtube_service
from app.services.content_pipeline import ContentRequest, run_content_generation
from app.services.image_fetcher import ImageDownloadError

logger = get_logger(__name__)

router = APIRouter(tags=["🖼️ 마케팅 콘텐츠 생성"])


def _parse_top_performers(raw: str | None, task_id: str) -> str:
    """
    Spring이 JSON 문자열로 넘긴 우수 성과 게시물 목록을 프롬프트용 텍스트로 변환합니다.

    파싱에 실패해도 예외를 던지지 않습니다. 레퍼런스는 품질 향상을 위한
    부가 정보일 뿐이라, 없다고 해서 생성을 막을 이유가 없습니다.
    """
    if not raw:
        return ""

    try:
        performers = json.loads(raw)
        if isinstance(performers, list) and performers:
            return marketing_prompts.format_top_performers(performers)
    except Exception as exc:
        logger.warning("[%s] topPerformers 파싱 실패: %s", task_id, exc)

    return ""


@router.post(
    "/api/marketing/generate",
    response_model=JobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="AI 마케팅 콘텐츠 생성 (텍스트 + 포스터 + 영상)",
    description="""
분위기태그, 해시태그, 사용자 프롬프트, 업로드 예약 일정을 기반으로 **AI가 마케팅 홍보 텍스트 → 포스터 이미지 → 숏폼 영상**을 순차적으로 생성합니다.

### ⚡ 비동기 처리
이 API는 **즉시 202 응답**과 `taskId`를 반환합니다. 실제 AI 생성 작업은 백그라운드에서 진행되며, 완료 시 환경 변수 `SPRING_WEBHOOK_URL`에 설정된 URL로 결과를 POST 전송합니다.

### 📬 웹훅 콜백 형식
작업 완료 시 다음 JSON이 Spring 백엔드로 전송됩니다:
```json
{
  "taskId": "a1b2c3d4-...",
  "scheduleId": "42",
  "status": "SUCCESS",
  "jobType": "GENERATE_CONTENT",
  "data": {
    "contentType": "POST 또는 VIDEO",
    "mode": "TRANSFORM 또는 ORIGINAL",
    "generatedText": "AI가 생성한 홍보 텍스트...",
    "posterUrl": "http://host/static/images/poster_xxx.png",
    "s3VideoUrl": "http://host/content/shortform_xxx.mp4",
    "localVideoPath": "static/videos/shortform_xxx.mp4",
    "uploadSchedule": "월요일 18:00",
    "createdAtMillis": 1716134400000
  }
}
```
""",
)
async def generate_content(
    request: Request,
    req: GenerateRequestDto,
    background_tasks: BackgroundTasks,
) -> JobAcceptedResponse:
    task_id = str(uuid.uuid4())

    # 정적 파일 URL을 만들 때 쓸 서버 주소 (프록시 뒤에 있으면 프록시 주소가 잡힙니다)
    base_url = str(request.base_url).rstrip("/")

    # --- 입력 이미지 준비 ---
    image_urls = req.resolved_image_urls
    if len(image_urls) > settings.MAX_IMAGE_URLS:
        raise HTTPException(
            status_code=400,
            detail=f"이미지 URL은 최대 {settings.MAX_IMAGE_URLS}장까지 제공해주세요.",
        )

    try:
        saved_image_paths = await image_fetcher.download_images(image_urls, task_id)
    except ImageDownloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # --- 프롬프트 부가 정보 준비 ---
    # 날씨 조회는 실패해도 기본 문구로 대체되므로 여기서 미리 처리합니다.
    # (requests 기반 동기 호출이라 스레드풀에서 실행해 이벤트 루프를 막지 않습니다)
    weather_data = await run_in_threadpool(weather_service.build_weather_context, req.lat, req.lon)
    top_performers_context = _parse_top_performers(req.resolved_top_performers, task_id)

    # --- 백그라운드 작업 등록 후 즉시 202 반환 ---
    background_tasks.add_task(
        run_content_generation,
        ContentRequest(
            task_id=task_id,
            base_url=base_url,
            content_type=req.resolved_content_type,
            mode=req.resolved_mode,
            saved_image_paths=saved_image_paths,
            schedule_id=req.resolved_schedule_id,
            mood_tag=req.resolved_mood_tag,
            hash_tag=req.resolved_hash_tag,
            user_prompt=req.resolved_prompt,
            upload_day=req.resolved_upload_day,
            upload_time=req.resolved_upload_time,
            weather_data=weather_data,
            top_performers_context=top_performers_context,
        ),
    )

    return JobAcceptedResponse(taskId=task_id)


@router.post(
    "/api/marketing/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_200_OK,
    summary="생성된 임시 영상을 YouTube에 업로드",
    description="""
미리보기가 확정된 영상을 YouTube에 업로드합니다.

### 요청 파라미터
- `scheduleId`: Spring Boot에서 받은 스케줄 식별자 (선택, 콜백 시 그대로 반환)
- `localVideoPath`: generate 웹훅에서 받은 로컬 비디오 파일 경로
- `title`, `description`, `tags`: YouTube 메타데이터
- `privacyStatus`: 공개 상태 (기본: unlisted)

### 에러 케이스
- 404: 지정한 로컬 비디오 파일이 서버에 존재하지 않음
- 400: 비디오 파일이 mp4 형식이 아니거나 크기가 0바이트
- 500: YouTube API 업로드 실패
""",
)
async def upload_generated_video(request: UploadRequest) -> UploadResponse:
    # 웹훅으로 내보낸 상대 경로("static/videos/xxx.mp4")를 절대 경로로 되돌립니다.
    video_path = settings.resolve_path(request.localVideoPath)

    # --- 업로드 전 파일 검증 ---
    if not video_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"해당 로컬 비디오 파일을 찾을 수 없습니다: {request.localVideoPath}",
        )

    if video_path.suffix.lower() != ".mp4":
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 비디오 형식입니다. MP4 파일만 업로드 가능합니다. (입력: {request.localVideoPath})",
        )

    file_size = video_path.stat().st_size
    if file_size == 0:
        raise HTTPException(
            status_code=400,
            detail=f"비디오 파일의 크기가 0입니다. 손상된 파일일 수 있습니다: {request.localVideoPath}",
        )

    try:
        logger.info(
            "YouTube 업로드 시작: %s (%d bytes, scheduleId=%s)",
            video_path, file_size, request.scheduleId,
        )

        # NOTE: 현재는 공개 상태를 항상 'unlisted'로 고정합니다.
        #       요청의 privacyStatus 값을 반영하려면 아래에 privacy_status=request.privacyStatus 를 넘기세요.
        youtube_url = await run_in_threadpool(
            youtube_service.upload_video,
            str(video_path),
            request.title,
            request.description,
            request.tags,
        )

        logger.info("YouTube 업로드 성공: %s", youtube_url)
        return UploadResponse(
            status="SUCCESS",
            scheduleId=request.scheduleId,
            youtubeUrl=youtube_url,
        )
    except Exception as exc:
        error_msg = f"YouTube 업로드 실패: {exc}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)
