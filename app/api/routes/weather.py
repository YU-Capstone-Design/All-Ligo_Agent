"""날씨 조회 라우터."""

from fastapi import APIRouter, HTTPException, Query

from app.core.logging_config import get_logger
from app.schemas.weather import WeatherInfo
from app.services import weather_service

logger = get_logger(__name__)

router = APIRouter(tags=["🌤️ 날씨 정보"])


@router.get(
    "/api/weather",
    response_model=WeatherInfo,
    summary="실시간 날씨 정보 조회",
    description="""
위도(lat)와 경도(lon) 좌표를 입력하면 해당 위치의 **실시간 날씨 정보**를 반환합니다.

### 사용 목적
- 마케팅 콘텐츠 생성 시 날씨 분위기를 반영하기 위한 사전 조회
- 프론트엔드에서 사용자에게 현재 날씨 정보를 표시

### 동작 방식
1. Open-Meteo API에 좌표 기반 날씨 데이터를 요청합니다.
2. WMO 기상 코드를 한글 설명(`맑음`, `비`, `눈` 등)으로 변환합니다.
3. 이미지 생성에 활용할 영어 시각적 분위기 힌트(`visualCue`)를 자동 생성합니다.

### 좌표 예시
| 도시 | 위도(lat) | 경도(lon) |
|------|-----------|----------|
| 서울 | 37.5665 | 126.9780 |
| 부산 | 35.1796 | 129.0756 |
| 대구 | 35.8714 | 128.6014 |
| 경산 | 35.8251 | 128.7413 |

### 에러 케이스
- Open-Meteo API 요청 실패 시 500 에러를 반환합니다.
""",
    responses={
        200: {"description": "날씨 정보 조회 성공"},
        500: {"description": "외부 날씨 API 호출 실패 (네트워크 오류 또는 타임아웃)"},
    },
)
async def get_weather(
    lat: float = Query(..., description="위도 (latitude). 예: 서울 37.5665, 대구 35.8714", example=35.8714),
    lon: float = Query(..., description="경도 (longitude). 예: 서울 126.9780, 대구 128.6014", example=128.6014),
) -> WeatherInfo:
    try:
        return WeatherInfo(**weather_service.fetch_weather_data(lat, lon))
    except Exception as exc:
        logger.error("날씨 조회 실패 (lat=%s, lon=%s): %s", lat, lon, exc)
        raise HTTPException(status_code=500, detail=f"날씨 정보를 가져오는데 실패했습니다: {exc}")
