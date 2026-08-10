"""이미지 분석(Vision-LLM) 라우터."""

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.core.logging_config import get_logger
from app.services import vision_service

logger = get_logger(__name__)

router = APIRouter(tags=["👁️ 이미지 분석 (Vision)"])


@router.post(
    "/api/vision/analyze",
    summary="이미지 분석 → 마케팅 키워드 추출",
    description="""
업로드된 이미지를 **Vision-LLM(LLaVA)**으로 분석하여 마케팅에 활용할 수 있는 키워드를 추출합니다.

### 분석 항목
| 항목 | 설명 | 예시 |
|------|------|------|
| `objects` | 이미지 속 주요 객체/사물 | coffee, croissant, table |
| `mood` | 이미지의 감성/분위기 | cozy, warm, inviting |
| `colors` | 지배적인 색상 | brown, cream, white |

### 응답 형식
```json
{
  "success": true,
  "filename": "cafe_photo.jpg",
  "analysis": {
    "objects": ["coffee", "croissant", "wooden table"],
    "mood": ["cozy", "warm", "inviting"],
    "colors": ["brown", "cream", "white"]
  }
}
```

### 주의사항
- 이미지 파일만 업로드 가능합니다 (JPG, PNG, WebP 등).
- LLaVA 모델이 Ollama에서 실행 중이어야 합니다 (`ollama pull llava`).
- 분석 시간: 약 5~15초 (이미지 크기 및 GPU 상태에 따라 상이)
""",
    responses={
        200: {"description": "이미지 분석 성공. objects, mood, colors 키워드 반환"},
        400: {"description": "이미지가 아닌 파일을 업로드한 경우"},
        500: {"description": "Vision-LLM 분석 실패 (Ollama 미실행 또는 모델 미설치)"},
    },
)
async def analyze_image(
    image: UploadFile = File(
        ...,
        description="분석할 이미지 파일. 지원 형식: JPG, PNG, WebP, GIF 등 일반 이미지 형식. Content-Type이 `image/`로 시작해야 합니다.",
    ),
) -> dict:
    # Content-Type이 없거나 image/ 로 시작하지 않으면 거부합니다.
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="이미지 파일만 업로드 가능합니다.")

    try:
        image_bytes = await image.read()

        # 분석은 동기 함수이므로 스레드풀에서 실행해 이벤트 루프를 막지 않습니다.
        analysis_result = await run_in_threadpool(
            vision_service.analyze_image_for_marketing, image_bytes
        )

        return {
            "success": True,
            "filename": image.filename,
            "analysis": analysis_result,
        }
    except Exception as exc:
        logger.error("이미지 분석 중 오류 발생: %s", exc)
        raise HTTPException(status_code=500, detail="이미지 분석에 실패했습니다.")
