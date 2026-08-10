"""실시간 날씨 조회 관련 응답 스키마."""

from typing import Optional

from pydantic import BaseModel, Field


class WeatherInfo(BaseModel):
    """실시간 날씨 정보 응답 모델입니다. Open-Meteo API에서 가져온 데이터를 가공하여 반환합니다."""

    weatherDesc: str = Field(
        ...,
        description="날씨 상태 한글 설명. 가능한 값: `맑음`, `구름 조금/흐림`, `안개`, `비`, `눈`, `뇌우/폭풍`",
        example="맑음",
    )
    temperature: Optional[float] = Field(None, description="현재 기온 (섭씨 °C)", example=23.5)
    visualCue: str = Field(
        ...,
        description="이미지 생성 프롬프트에 삽입할 시각적 분위기 힌트 (영어). 날씨에 따라 자동 결정됩니다.",
        example="sunny, bright, clear sky, vibrant",
    )
    weatherCode: Optional[int] = Field(
        None,
        description="WMO 기상 코드. 0=맑음, 1~3=구름, 45/48=안개, 51~82=비/이슬비, 71~86=눈, 95~99=뇌우. "
                    "상세: https://open-meteo.com/en/docs#weathervariables",
        example=0,
    )
    precipitation: Optional[float] = Field(None, description="현재 강수량 (mm). 0이면 비/눈 없음", example=0.0)
    humidity: Optional[int] = Field(None, description="상대 습도 (0~100%)", example=55)
    windSpeed: Optional[float] = Field(None, description="지상 10m 풍속 (m/s)", example=3.2)
    apparentTemperature: Optional[float] = Field(None, description="체감 온도 (섭씨 °C). 풍속·습도를 고려한 값", example=22.1)
    isDay: Optional[int] = Field(None, description="주간 여부. 1=낮(일출~일몰), 0=밤", example=1)
