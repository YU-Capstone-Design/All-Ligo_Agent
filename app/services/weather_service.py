"""
Open-Meteo 기반 실시간 날씨 조회 서비스.

두 가지 형태로 날씨를 제공합니다.
1. `fetch_weather_data()`  : /api/weather 응답용 구조화 데이터(dict)
2. `build_weather_context()`: LLM 프롬프트에 삽입할 한국어 서술 문장
"""

from typing import Optional

import requests

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# WMO 기상 코드 → (한글 설명, 이미지 생성용 영어 분위기 힌트) 매핑.
# 원래는 if/elif 사슬이었지만, 코드 추가 시 매핑만 고치면 되도록 테이블로 분리했습니다.
# 참고: https://open-meteo.com/en/docs#weathervariables
_WEATHER_CODE_MAP: dict[int, tuple[str, str]] = {}


def _register(codes: list[int], desc: str, visual_cue: str) -> None:
    """여러 기상 코드를 같은 (설명, 분위기 힌트) 조합에 등록합니다."""
    for code in codes:
        _WEATHER_CODE_MAP[code] = (desc, visual_cue)


_register([1, 2, 3], "구름 조금/흐림", "cloudy, soft lighting, overcast")
_register([45, 48], "안개", "foggy, mysterious, misty, muted colors")
_register(
    [51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82],
    "비",
    "rainy, wet streets, puddles, cinematic moody lighting, water drops",
)
_register([71, 73, 75, 77, 85, 86], "눈", "snowy, winter wonderland, falling snow, cold, cozy")
_register([95, 96, 99], "뇌우/폭풍", "stormy, lightning, dark dramatic clouds, heavy rain")

# 매핑에 없는 코드(0 = 맑음 포함)의 기본값
_DEFAULT_WEATHER = ("맑음", "sunny, bright, clear sky, vibrant")


def fetch_weather_data(lat: float, lon: float) -> dict:
    """
    좌표를 받아 Open-Meteo에서 현재 날씨를 조회하고 응답 스키마에 맞춰 가공합니다.

    네트워크 오류나 4xx/5xx 응답은 그대로 예외로 전파합니다.
    호출부에서 상황에 맞게 처리하세요.
    """
    url = (
        f"{settings.WEATHER_API_URL}?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,apparent_temperature,is_day,"
        "precipitation,weather_code,wind_speed_10m"
    )

    response = requests.get(url, timeout=settings.WEATHER_TIMEOUT_SEC)
    response.raise_for_status()
    current = response.json().get("current", {})

    weather_code = current.get("weather_code")
    weather_desc, visual_cue = _WEATHER_CODE_MAP.get(weather_code, _DEFAULT_WEATHER)

    return {
        "weatherDesc": weather_desc,
        "temperature": current.get("temperature_2m"),
        "visualCue": visual_cue,
        "weatherCode": weather_code,
        "precipitation": current.get("precipitation"),
        "humidity": current.get("relative_humidity_2m"),
        "windSpeed": current.get("wind_speed_10m"),
        "apparentTemperature": current.get("apparent_temperature"),
        "isDay": current.get("is_day"),
    }


def build_weather_context(lat: Optional[float], lon: Optional[float]) -> str:
    """
    LLM 프롬프트의 [실시간 날씨 컨텍스트] 블록에 넣을 한국어 서술을 만듭니다.

    좌표가 없거나 조회에 실패해도 예외를 던지지 않고 기본 문구를 반환합니다.
    날씨는 어디까지나 부가 정보이므로, 이것 때문에 콘텐츠 생성 전체가
    실패해서는 안 되기 때문입니다.
    """
    if lat is None or lon is None:
        return "날씨 정보 없음 (기본 설정: 맑음, 기온 20도). 밝고 긍정적인 분위기로 작성해주세요."

    try:
        w = fetch_weather_data(lat, lon)
    except Exception as exc:
        logger.warning("날씨 API 조회 실패: %s", exc)
        return "날씨 정보 조회 실패 (기본 설정: 맑음). 밝고 긍정적인 분위기로 작성해주세요."

    is_day_str = "낮" if w.get("isDay") == 1 else "밤"

    context = (
        f"현재 위치의 날씨는 '{w['weatherDesc']}'이며, 기온은 {w['temperature']}도"
        f"(체감 {w['apparentTemperature']}도)입니다. "
        f"현재 시간대는 {is_day_str}이며, 습도는 {w['humidity']}%, 풍속은 {w['windSpeed']}m/s입니다. "
    )

    # 강수량이 있을 때만 비/눈 문장을 덧붙입니다.
    if (w.get("precipitation") or 0) > 0:
        context += f"현재 강수량은 {w['precipitation']}mm로 비나 눈이 내리고 있습니다. "

    context += (
        f"이미지 프롬프트 작성 시 시각적 분위기 힌트({w['visualCue']})를 적극 활용하여 "
        "현장감 있는 홍보물을 만드세요."
    )
    return context
