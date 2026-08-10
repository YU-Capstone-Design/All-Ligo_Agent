"""YouTube 업로드 요청/응답 스키마."""

from typing import List, Optional

from pydantic import BaseModel, Field


class UploadRequest(BaseModel):
    """YouTube 업로드 요청 모델. Spring Boot에서 Webhook으로 받은 localVideoPath를 그대로 전달합니다."""

    scheduleId: Optional[str] = Field(None, description="Spring Boot 스케줄 DB 식별자. 업로드 완료 후 콜백 시 그대로 반환됩니다.")
    localVideoPath: str = Field(..., description="서버 로컬에 저장된 비디오 경로 (예: static/videos/shortform_xxx.mp4)")
    title: str = Field(..., description="유튜브 영상 제목")
    description: str = Field(..., description="유튜브 영상 설명")
    tags: List[str] = Field(default=[], description="유튜브 영상 태그 리스트")
    privacyStatus: str = Field(
        default="unlisted",
        description="유튜브 영상 공개 상태. 가능한 값: public, unlisted, private",
        example="unlisted",
    )


class UploadResponse(BaseModel):
    """YouTube 업로드 결과 응답 모델."""

    status: str = Field(..., description="업로드 결과 상태: SUCCESS 또는 FAILED")
    scheduleId: Optional[str] = Field(None, description="요청 시 전달받은 스케줄 ID")
    youtubeUrl: Optional[str] = Field(None, description="업로드된 YouTube 영상 URL")
    error: Optional[str] = Field(None, description="실패 시 에러 메시지")
