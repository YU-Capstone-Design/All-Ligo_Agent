"""
마케팅 콘텐츠 생성 파이프라인 (백그라운드 워커).

`/api/marketing/generate` 요청을 받으면 라우터는 즉시 202를 반환하고,
실제 생성 작업은 이 모듈이 백그라운드에서 수행합니다.

전체 흐름
    1. mode 해석 (AUTO → ORIGINAL/TRANSFORM)
    2. 업로드 이미지 분석(LLaVA) 및 원본 이미지 배치
    3. 마케팅 텍스트 생성 (Ollama)
    4. 포스터 이미지 생성 (TRANSFORM + VIDEO 조합에서만)
    5. 숏폼 영상 렌더링 + S3 업로드 (VIDEO에서만)
    6. Spring 백엔드로 결과 웹훅 전송

각 단계는 아래 private 헬퍼로 나뉘어 있고, `run_content_generation()` 이 조립합니다.
"""

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi.concurrency import run_in_threadpool
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_ollama import ChatOllama

from app.core.config import settings
from app.core.logging_config import get_logger
from app.core.state import job_tracker
from app.prompts import marketing as marketing_prompts
from app.services import (
    image_service,
    storage_service,
    text_cleaner,
    vision_service,
    webhook_service,
)
from app.services.video import create_shortform_video

logger = get_logger(__name__)

# 한 번의 요청으로 만들어낼 최대 포스터 이미지 수
_MAX_POSTER_IMAGES = 3


@dataclass
class ContentRequest:
    """
    백그라운드 워커가 필요로 하는 입력값 묶음.

    기존에는 워커 함수가 13개의 개별 인자를 받아 호출부가 매우 장황했습니다.
    한 덩어리로 묶어 전달 인자를 추가·변경하기 쉽게 만들었습니다.
    """

    task_id: str
    base_url: str            # 정적 파일 URL을 만들 때 쓰는 서버 주소 (예: http://1.2.3.4:8000)
    content_type: str        # POST | VIDEO
    mode: str                # TRANSFORM | ORIGINAL | AUTO
    saved_image_paths: List[Path]  # 요청으로 받아 로컬에 저장한 원본 이미지들
    schedule_id: Optional[str]
    mood_tag: str
    hash_tag: str
    user_prompt: str
    upload_day: str
    upload_time: str
    weather_data: str        # 프롬프트에 넣을 날씨 서술
    top_performers_context: str = ""

    def resolve_mode(self) -> str:
        """
        AUTO 모드를 실제 모드로 해석합니다.

        업로드된 이미지가 있으면 그것을 그대로 쓰고(ORIGINAL),
        없으면 AI가 새로 만들어야 합니다(TRANSFORM).
        """
        if self.mode.upper() != "AUTO":
            return self.mode
        return "ORIGINAL" if self.saved_image_paths else "TRANSFORM"


@dataclass
class _ImageAssets:
    """생성 과정에서 모인 포스터 이미지들의 파일명과 공개 URL."""

    filenames: List[str] = field(default_factory=list)
    urls: List[str] = field(default_factory=list)

    def add(self, filename: str, base_url: str) -> None:
        self.filenames.append(filename)
        self.urls.append(f"{base_url}/static/images/{filename}")

    @property
    def poster_url(self) -> Optional[str]:
        """대표 포스터 URL (첫 번째 이미지). 없으면 None."""
        return self.urls[0] if self.urls else None


# ---------------------------------------------------------------------------
# 1단계: 이미지 준비
# ---------------------------------------------------------------------------

def _copy_to_static(source: Path, task_id: str, index: int) -> str:
    """
    원본 이미지를 static/images 로 복사하고 파일명을 반환합니다.

    업로드 임시 파일은 작업 종료 후 삭제되므로, 계속 서빙해야 하는 이미지는
    별도 위치로 옮겨둬야 합니다.
    """
    ext = source.suffix or ".png"
    filename = f"poster_{task_id}_{index}{ext}"
    shutil.copy(source, settings.IMAGES_DIR / filename)
    return filename


async def _prepare_source_images(req: ContentRequest, resolved_mode: str) -> Tuple[str, _ImageAssets]:
    """
    업로드된 이미지를 분석하고, 필요한 경우 static 으로 복사합니다.

    Returns:
        (프롬프트에 삽입할 이미지 분석 블록, 수집된 이미지 자산)
        업로드 이미지가 없으면 ("", 빈 자산) 을 반환합니다.
    """
    assets = _ImageAssets()

    if not req.saved_image_paths:
        return "", assets

    # 대표 이미지 1장만 분석합니다. 여러 장을 돌리면 시간이 배로 늘어납니다.
    first_image = req.saved_image_paths[0]
    logger.info("[%s] 업로드 이미지 분석(LLaVA) 시작...", req.task_id)

    image_bytes = first_image.read_bytes()
    analysis_result = await run_in_threadpool(
        vision_service.analyze_image_for_marketing, image_bytes
    )
    vision_section = marketing_prompts.build_vision_section(analysis_result)

    if req.content_type == "POST":
        # 포스트는 대표 이미지 1장만 사용합니다.
        assets.add(_copy_to_static(first_image, req.task_id, 0), req.base_url)

    elif req.content_type == "VIDEO" and resolved_mode == "ORIGINAL":
        # 원본 모드 영상은 업로드된 이미지를 전부 프레임 소재로 씁니다.
        logger.info(
            "[%s] ORIGINAL 모드 — AI 이미지 생성을 건너뛰고 업로드 이미지 %d장을 사용합니다.",
            req.task_id, len(req.saved_image_paths),
        )
        for i, path in enumerate(req.saved_image_paths):
            assets.add(_copy_to_static(path, req.task_id, i), req.base_url)

    return vision_section, assets


# ---------------------------------------------------------------------------
# 2단계: 마케팅 텍스트 생성
# ---------------------------------------------------------------------------

async def _generate_marketing_text(
    req: ContentRequest, resolved_mode: str, vision_section: str
) -> str:
    """
    Ollama LLM으로 홍보 텍스트(및 필요 시 이미지 프롬프트)를 생성합니다.

    반환값은 후처리 전의 **원본 응답**입니다. 이미지 프롬프트 추출과 텍스트 정리는
    호출부에서 각각 수행합니다.
    """
    chat_model = ChatOllama(
        model=settings.OLLAMA_TEXT_MODEL,
        temperature=settings.OLLAMA_TEXT_TEMPERATURE,
        base_url=settings.OLLAMA_BASE_URL,
        # 생성이 끝나면 GPU 메모리를 즉시 반납합니다 (이미지 생성과 VRAM을 나눠 써야 함).
        keep_alive=0,
    )

    prompt_text = marketing_prompts.build_marketing_prompt(
        content_type=req.content_type,
        resolved_mode=resolved_mode,
        vision_section=vision_section,
        performers_section=marketing_prompts.build_top_performers_section(
            req.top_performers_context
        ),
    )

    chain = PromptTemplate.from_template(prompt_text) | chat_model | StrOutputParser()

    logger.info("[%s] Ollama(%s)로 텍스트 생성 중...", req.task_id, settings.OLLAMA_TEXT_MODEL)
    return chain.invoke(
        {
            "weather_data": req.weather_data,
            "mood_tag": req.mood_tag,
            "hash_tag": req.hash_tag,
            "user_prompt": req.user_prompt,
            "upload_day": req.upload_day,
            "upload_time": req.upload_time,
        }
    )


# ---------------------------------------------------------------------------
# 3단계: 포스터 이미지 생성
# ---------------------------------------------------------------------------

async def _generate_poster_images(req: ContentRequest, raw_text: str, assets: _ImageAssets) -> None:
    """
    LLM이 뽑아준 영어 프롬프트로 포스터 이미지를 생성해 `assets` 에 추가합니다.

    이미지 생성 실패는 치명적이지 않으므로(텍스트만으로도 결과를 낼 수 있음)
    예외를 삼키고 로그만 남깁니다.
    """
    image_prompts = text_cleaner.extract_image_prompts(raw_text, limit=_MAX_POSTER_IMAGES)
    if not image_prompts:
        return

    logger.info("[%s] 로컬 SDXL/FLUX로 포스터 %d장 생성 중...", req.task_id, len(image_prompts))
    try:
        for prompt in image_prompts:
            filename = await run_in_threadpool(image_service.generate_image, prompt)
            if filename:
                assets.add(filename, req.base_url)
    except Exception as exc:
        logger.error("[%s] 이미지 생성 실패: %s", req.task_id, exc)


# ---------------------------------------------------------------------------
# 4단계: 영상 렌더링 및 S3 업로드
# ---------------------------------------------------------------------------

async def _render_video(
    req: ContentRequest, assets: _ImageAssets, clean_text: str
) -> Tuple[Optional[str], Optional[str]]:
    """
    이미지들로 숏폼 영상을 만들고 S3에 업로드합니다.

    Returns:
        (S3 공개 URL, 프로젝트 루트 기준 로컬 경로). 각각 실패 시 None.
        영상은 만들어졌는데 S3 업로드만 실패하는 경우도 있으므로 두 값은 독립적입니다.
    """
    if not assets.filenames:
        logger.warning("[%s] 영상 생성에 사용할 이미지가 없습니다.", req.task_id)
        return None, None

    logger.info(
        "[%s] FFmpeg 영상 생성 시작 (이미지 %d장)...", req.task_id, len(assets.filenames)
    )

    try:
        local_image_paths = [str(settings.IMAGES_DIR / fn) for fn in assets.filenames]
        filename = await run_in_threadpool(
            create_shortform_video, local_image_paths, clean_text
        )
    except Exception as exc:
        logger.error("[%s] 영상 생성 실패: %s", req.task_id, exc)
        return None, None

    video_path = settings.VIDEOS_DIR / filename
    # 웹훅으로 내보내는 경로는 예전과 동일하게 "static/videos/xxx.mp4" 상대 경로 형식을 유지합니다.
    local_video_path = settings.to_relative(video_path)

    # 영상 생성은 성공했으므로, S3 업로드가 실패해도 로컬 경로는 그대로 돌려줍니다.
    s3_video_url = None
    try:
        logger.info("[%s] 생성된 영상을 S3에 업로드 중...", req.task_id)
        s3_video_url = await run_in_threadpool(
            storage_service.upload_video_to_s3, str(video_path), f"content/{filename}"
        )
    except Exception as exc:
        logger.error("[%s] S3 업로드 실패: %s", req.task_id, exc)

    return s3_video_url, local_video_path


# ---------------------------------------------------------------------------
# 정리
# ---------------------------------------------------------------------------

def _cleanup_uploads(req: ContentRequest) -> None:
    """요청과 함께 받은 원본 이미지 임시 파일을 삭제합니다."""
    for path in req.saved_image_paths:
        try:
            if path and path.exists():
                path.unlink()
        except Exception as exc:
            logger.debug("[%s] 임시 파일 삭제 실패(무시): %s (%s)", req.task_id, path, exc)


# ---------------------------------------------------------------------------
# 파이프라인 진입점
# ---------------------------------------------------------------------------

async def run_content_generation(req: ContentRequest) -> None:
    """
    콘텐츠 생성 전체 파이프라인을 실행하고 결과를 웹훅으로 전송합니다.

    BackgroundTasks에서 호출되므로 예외를 밖으로 던지지 않습니다.
    어떤 단계에서 실패하든 FAILED 웹훅을 보내 Spring 쪽이 계속 기다리지 않도록 합니다.
    """
    with job_tracker.track():
        try:
            resolved_mode = req.resolve_mode()

            # 1. 업로드 이미지 분석 + 배치
            vision_section, assets = await _prepare_source_images(req, resolved_mode)

            # 2. 마케팅 텍스트 생성
            raw_text = await _generate_marketing_text(req, resolved_mode, vision_section)

            # 3. 포스터 이미지 생성 (TRANSFORM + VIDEO 조합에서만)
            if marketing_prompts.needs_image_prompts(req.content_type, resolved_mode):
                await _generate_poster_images(req, raw_text, assets)

            # 4. 응답용 텍스트 정리 (메타 접두사·해시태그·이미지 프롬프트 제거)
            clean_text = text_cleaner.clean_marketing_text(raw_text)

            # 5. 영상 렌더링 (VIDEO 일 때만)
            s3_video_url: Optional[str] = None
            local_video_path: Optional[str] = None
            if req.content_type == "VIDEO":
                s3_video_url, local_video_path = await _render_video(req, assets, clean_text)
            else:
                logger.info("[%s] contentType이 POST이므로 영상 생성을 건너뜁니다.", req.task_id)

            # 6. 성공 웹훅
            logger.info("[%s] 작업 완료. 결과를 전송합니다.", req.task_id)
            webhook_service.send_success(
                task_id=req.task_id,
                schedule_id=req.schedule_id,
                data={
                    "contentType": req.content_type,
                    # 요청받은 원본 mode 값을 그대로 돌려줍니다(AUTO 포함).
                    "mode": req.mode,
                    "generatedText": clean_text,
                    "posterUrl": assets.poster_url,
                    "s3VideoUrl": s3_video_url,
                    "localVideoPath": local_video_path,
                    "uploadSchedule": f"{req.upload_day} {req.upload_time}",
                    "createdAtMillis": int(time.time() * 1000),
                },
            )

        except Exception as exc:
            logger.exception("[%s] 작업 실패: %s", req.task_id, exc)
            webhook_service.send_failure(
                task_id=req.task_id,
                schedule_id=req.schedule_id,
                error=str(exc),
            )
        finally:
            _cleanup_uploads(req)
