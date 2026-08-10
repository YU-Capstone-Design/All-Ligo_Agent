"""마케팅 콘텐츠 생성 요청/응답 스키마."""

from typing import List, Optional, Union

from pydantic import BaseModel, Field


class JobAcceptedResponse(BaseModel):
    """비동기 작업이 수락되었을 때 반환되는 응답 모델입니다. 클라이언트는 이 taskId로 웹훅 결과를 매칭합니다."""

    taskId: str = Field(
        ...,
        description="생성된 고유 작업 ID (UUID v4 형식). 이 ID는 작업 완료 시 웹훅 콜백의 `taskId` 필드와 동일합니다.",
        example="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    )
    status: str = Field(
        "PROCESSING",
        description="작업 상태. 수락 시 항상 `PROCESSING`. 이후 웹훅으로 `SUCCESS` 또는 `FAILED`가 전달됩니다.",
        example="PROCESSING",
    )
    message: str = Field(
        "Background task started",
        description="작업 수락 안내 메시지",
        example="Background task started",
    )


class ContentResult(BaseModel):
    """마케팅 콘텐츠 생성 완료 시 웹훅으로 전송되는 데이터 모델입니다 (참고용 - 직접 반환되지 않음)."""

    contentType: str = Field(..., description="POST 또는 VIDEO")
    mode: str = Field(..., description="TRANSFORM 또는 ORIGINAL")
    generatedText: str = Field(..., description="Gemma4가 생성한 마케팅 텍스트 (블로그 글 또는 영상 자막)")
    posterUrl: Optional[str] = Field(None, description="AI이미지 또는 원본이미지의 접근 URL. 실패 시 null")
    s3VideoUrl: Optional[str] = Field(None, description="비디오 생성 시에만 존재, 없으면 null")
    localVideoPath: Optional[str] = Field(None, description="생성된 비디오의 서버 내 로컬 파일 경로. 없으면 null")
    uploadSchedule: str = Field(..., description="요청 시 지정한 업로드 예약 일정 (요일 + 시간)")
    createdAtMillis: int = Field(..., description="콘텐츠 생성 완료 시각 (Unix 밀리초 타임스탬프)")


class GenerateRequestDto(BaseModel):
    """
    콘텐츠 생성 요청 본문.

    호출하는 클라이언트(Spring 백엔드 / 프론트엔드)에 따라 camelCase 와 snake_case가
    섞여 들어오기 때문에 두 표기를 모두 필드로 받습니다. 라우터에서 매번
    `req.moodTag or req.mood_tag or "밝은"` 같은 식으로 풀어쓰는 대신,
    아래 `resolved_*` 프로퍼티에서 우선순위와 기본값을 한 번에 정리합니다.

    우선순위 규칙: camelCase 값이 있으면 그것을, 없으면 snake_case, 둘 다 없으면 기본값.
    """

    # --- 마케팅 정보 ---
    moodTag: Optional[str] = None
    mood_tag: Optional[str] = None
    hashTag: Optional[str] = None
    hash_tag: Optional[str] = None
    prompt: Optional[str] = None

    # --- 업로드 예약 일정 ---
    uploadDay: Optional[str] = None
    upload_day: Optional[str] = None
    uploadTime: Optional[str] = None
    upload_time: Optional[str] = None

    # --- 연동 식별자 ---
    scheduleId: Optional[Union[str, int]] = None
    schedule_id: Optional[Union[str, int]] = None

    # --- 위치(날씨 반영용) ---
    lat: Optional[float] = None
    lon: Optional[float] = None

    # --- 생성 옵션 ---
    contentType: Optional[str] = None   # POST | VIDEO
    content_type: Optional[str] = None
    mode: Optional[str] = None          # TRANSFORM | ORIGINAL | AUTO

    # --- 입력 이미지 ---
    imageUrls: Optional[List[str]] = None
    image_urls: Optional[List[str]] = None

    # --- 과거 우수 성과 게시물 (JSON 문자열) ---
    topPerformers: Optional[str] = None
    top_performers: Optional[str] = None

    # ------------------------------------------------------------------
    # 표기 통일 프로퍼티
    # ------------------------------------------------------------------
    @property
    def resolved_mood_tag(self) -> str:
        return self.moodTag or self.mood_tag or "밝은"

    @property
    def resolved_hash_tag(self) -> str:
        return self.hashTag or self.hash_tag or "#마케팅"

    @property
    def resolved_prompt(self) -> str:
        return self.prompt or ""

    @property
    def resolved_upload_day(self) -> str:
        return self.uploadDay or self.upload_day or "월요일"

    @property
    def resolved_upload_time(self) -> str:
        return self.uploadTime or self.upload_time or "18:00"

    @property
    def resolved_content_type(self) -> str:
        return self.contentType or self.content_type or "POST"

    @property
    def resolved_mode(self) -> str:
        return self.mode or "ORIGINAL"

    @property
    def resolved_schedule_id(self) -> Optional[str]:
        """scheduleId 는 int 로 올 수도 있어 항상 문자열로 정규화합니다."""
        raw = self.scheduleId if self.scheduleId is not None else self.schedule_id
        return str(raw) if raw is not None else None

    @property
    def resolved_image_urls(self) -> List[str]:
        """빈 문자열·공백만 있는 URL은 제외한 목록을 반환합니다."""
        urls = self.imageUrls if self.imageUrls is not None else (self.image_urls or [])
        return [url.strip() for url in urls if url.strip()]

    @property
    def resolved_top_performers(self) -> Optional[str]:
        return self.topPerformers or self.top_performers
