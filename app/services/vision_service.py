"""
Vision-LLM(LLaVA) 기반 이미지 분석 서비스. (기존 vision_analyzer.py)

업로드된 이미지에서 마케팅에 활용할 키워드(주요 객체 / 분위기 / 색감)를 추출합니다.
LangChain을 거치지 않고 Ollama의 /api/chat 을 직접 호출해서 오버헤드를 줄였습니다.
"""

import base64
from io import BytesIO

import requests
from PIL import Image

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# LLaVA 입력 이미지의 최대 해상도. 원본을 그대로 보내면 인코딩·추론이 크게 느려집니다.
_MAX_IMAGE_SIZE = (800, 800)
_JPEG_QUALITY = 85

# 모델이 "room", "food" 같은 뭉뚱그린 단어를 내놓지 않도록 구체성을 강하게 요구하는 프롬프트.
_ANALYSIS_PROMPT = (
    "You are an expert marketing analyst. Look at this image and extract key elements for an advertisement. "
    "CRITICAL INSTRUCTION: Be highly specific about the objects and environment in the image. DO NOT use generic, broad terms. "
    "Identify the exact type of space, food, or item. For example: "
    "- For spaces: use 'vacant commercial office space' or 'empty retail shop' instead of just 'room' or 'space'. "
    "- For food/pastries: use 'Mont Blanc pastry with chestnut cream' instead of 'bread' or 'pastry'; use 'grilled ribeye steak' instead of 'food' or 'meat'. "
    "- For objects: use 'wooden dining table' instead of 'table'.\n"
    "Provide your analysis exactly in this format:\n"
    "Objects: [highly specific main objects separated by comma]\n"
    "Mood: [emotional mood and vibe separated by comma]\n"
    "Colors: [dominant colors separated by comma]"
)

# 모델 응답에서 파싱할 "라인 접두사 → 결과 딕셔너리 키" 매핑
_RESULT_PREFIXES = {
    "Objects:": "objects",
    "Mood:": "mood",
    "Colors:": "colors",
}


def encode_image_to_base64(image_bytes: bytes) -> str:
    """
    이미지 바이트를 Base64 문자열로 변환합니다.

    LLaVA 처리 속도를 위해 최대 800x800으로 축소하고 JPEG로 재압축합니다.
    리사이징에 실패하면(손상된 파일 등) 원본 바이트를 그대로 인코딩합니다.
    """
    try:
        img = Image.open(BytesIO(image_bytes))
        img.thumbnail(_MAX_IMAGE_SIZE)

        # JPEG는 알파 채널을 지원하지 않으므로 RGB로 변환합니다.
        if img.mode != "RGB":
            img = img.convert("RGB")

        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=_JPEG_QUALITY)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")
    except Exception as exc:
        logger.warning("이미지 리사이징 오류(원본 데이터 사용): %s", exc)
        return base64.b64encode(image_bytes).decode("utf-8")


def _parse_analysis_text(result_text: str) -> dict:
    """
    모델의 평문 응답을 딕셔너리로 파싱합니다.

    기대 형식:
        Objects: coffee, croissant
        Mood: cozy, warm
        Colors: brown, cream
    """
    analysis: dict = {"objects": [], "mood": [], "colors": []}

    for line in result_text.split("\n"):
        # LLaVA 는 첫 줄을 공백으로 시작하거나("  Objects: ..."), 마크다운 기호
        # ("**Objects:**", "- Objects:")를 붙이곤 합니다. 그대로 비교하면 매칭에 실패해
        # 해당 항목이 통째로 빠지므로, 앞뒤 기호를 걷어내고 대소문자 무시로 비교합니다.
        normalized = line.strip().lstrip("-*• ").replace("**", "")
        for prefix, key in _RESULT_PREFIXES.items():
            if normalized.lower().startswith(prefix.lower()):
                values = normalized[len(prefix):].split(",")
                analysis[key] = [v.strip() for v in values if v.strip()]
                break

    return analysis


def analyze_image_for_marketing(image_bytes: bytes) -> dict:
    """
    이미지를 분석해 {"objects": [...], "mood": [...], "colors": [...]} 를 반환합니다.

    이 함수는 **동기(blocking)** 입니다. FastAPI 라우터에서 호출할 때는 반드시
    `run_in_threadpool()` 로 감싸서 이벤트 루프를 막지 않도록 하세요.

    Ollama 호출이 실패하면 예외를 던지지 않고 빈 결과 + "error" 키를 반환합니다.
    이미지 분석은 부가 기능이라 실패해도 콘텐츠 생성은 계속되어야 하기 때문입니다.
    """
    base64_image = encode_image_to_base64(image_bytes)

    payload = {
        "model": settings.OLLAMA_VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": _ANALYSIS_PROMPT,
                "images": [base64_image],
            }
        ],
        "options": {
            "temperature": 0.2,
            # 컨텍스트를 과도하게 잡으면(32768 등) VRAM 점유가 커지고 hang이 발생합니다.
            "num_ctx": 4096,
        },
        "stream": False,
        # 분석이 끝나면 Ollama가 GPU 메모리를 즉시 반납하도록 합니다.
        "keep_alive": 0,
    }

    logger.info("Vision-LLM(%s)을 통해 이미지 분석 중...", settings.OLLAMA_VISION_MODEL)
    try:
        response = requests.post(
            f"{settings.OLLAMA_BASE_URL}/api/chat",
            json=payload,
            timeout=settings.OLLAMA_VISION_TIMEOUT_SEC,
        )
        response.raise_for_status()
        result_text = response.json().get("message", {}).get("content", "")
    except Exception as exc:
        logger.error("Ollama API 호출 중 오류 발생: %s", exc)
        return {"objects": [], "mood": [], "colors": [], "error": str(exc)}

    try:
        return _parse_analysis_text(result_text)
    except Exception as exc:
        logger.error("결과 파싱 오류: %s", exc)
        return {"objects": [], "mood": [], "colors": [], "raw_text": result_text}
