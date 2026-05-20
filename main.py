import os
import time
import uuid
import requests
import shutil
import subprocess
import json
from typing import Optional, List
from fastapi import FastAPI, Form, UploadFile, File, Request, BackgroundTasks, status, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from langchain_core.prompts import PromptTemplate
from langchain_ollama import ChatOllama
from langchain_core.output_parsers import StrOutputParser

app = FastAPI(
    title="📢 All-Ligo 마케팅 AI 에이전트 API",
    description="""
## 소상공인을 위한 AI 마케팅 콘텐츠 자동 생성 서버

이 API는 **100% 로컬 AI 모델**을 활용하여 소상공인의 마케팅 콘텐츠(텍스트, 포스터 이미지, 숏폼 영상)를 자동으로 생성합니다.

### 🏗️ 시스템 아키텍처
- **텍스트 생성**: Ollama + Qwen2.5:3b (로컬 LLM)
- **이미지 생성**: Stable Diffusion XL (로컬 GPU - RTX 4080)
- **영상 생성**: FFmpeg 기반 Ken Burns 효과 + 트랜지션
- **이미지 분석**: LLaVA Vision-LLM (로컬)
- **날씨 연동**: Open-Meteo API (외부)

### 🔄 비동기 처리 흐름
1. Spring 백엔드가 콘텐츠 생성을 요청합니다.
2. 이 서버는 즉시 `202 Accepted`와 `taskId`를 반환합니다.
3. 백그라운드에서 AI 생성 작업이 진행됩니다.
4. 작업 완료 시, Spring 백엔드의 웹훅 URL로 결과를 전송합니다.

### ⚙️ 환경 변수
- `SPRING_WEBHOOK_URL`: 작업 완료 시 결과를 전송할 Spring 백엔드 콜백 URL (기본값: `http://localhost:8080/api/internal/content-callback`)
""",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {
            "name": "🖼️ 마케팅 콘텐츠 생성",
            "description": "AI를 활용한 마케팅 텍스트, 포스터 이미지, 숏폼 영상을 비동기로 생성합니다. 요청 즉시 `taskId`를 반환하며, 완료 시 웹훅으로 결과를 전송합니다.",
        },
        {
            "name": "🌤️ 날씨 정보",
            "description": "Open-Meteo API를 통해 실시간 날씨 정보를 조회합니다. 마케팅 콘텐츠 생성 시 날씨 분위기를 반영하는 데 사용됩니다.",
        },
        {
            "name": "🖥️ 시스템 모니터링",
            "description": "서버의 현재 상태(GPU, 디스크, 동시 작업 수)를 조회하여 작업 가능 여부를 확인합니다.",
        },
        {
            "name": "👁️ 이미지 분석 (Vision)",
            "description": "Vision-LLM(LLaVA)을 사용하여 업로드된 이미지에서 마케팅 키워드(객체, 분위기, 색감)를 추출합니다.",
        },
        {
            "name": "🏠 홈",
            "description": "서버 접속 확인용 테스트 페이지입니다.",
        },
    ],
)

os.makedirs("static/videos", exist_ok=True)
os.makedirs("static/images", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

import image_generator
import video_generator
import vision_analyzer

# --- 시스템 상태 추적용 전역 변수 ---
active_jobs_count = 0
MAX_CONCURRENT_JOBS = 2

class GpuStatus(BaseModel):
    """GPU 상태 정보를 담는 모델입니다. nvidia-smi를 통해 조회됩니다."""
    gpuUtil: int = Field(..., description="GPU 연산 유닛 사용률 (0~100%). 90% 이상이면 과부하 상태입니다.", example=45)
    gpuMemUtil: int = Field(..., description="GPU 메모리 사용률 (0~100%)", example=62)
    gpuMemUsedMb: int = Field(..., description="현재 사용 중인 GPU 메모리 (MB 단위)", example=10240)
    gpuMemTotalMb: int = Field(..., description="GPU 전체 메모리 용량 (MB 단위). RTX 4080 기준 16384MB", example=16384)

class SystemStatusResponse(BaseModel):
    """서버 시스템 상태 응답 모델입니다. Spring 백엔드에서 작업 요청 전 서버 가용 여부를 판단하는 데 사용합니다."""
    status: str = Field(
        ...,
        description="서버 가용 상태. `available`(작업 수락 가능) 또는 `busy`(작업 거부 권장). "
                    "다음 조건 중 하나라도 해당하면 busy: (1) 동시 작업 수 ≥ 최대 허용치, (2) 디스크 여유 ≤ 1GB, (3) GPU 사용률 ≥ 90%",
        example="available"
    )
    activeJobs: int = Field(..., description="현재 백그라운드에서 실행 중인 AI 생성 작업 수", example=0)
    maxConcurrentJobs: int = Field(..., description="서버가 허용하는 최대 동시 작업 수 (현재 고정값: 2)", example=2)
    diskSpaceFreeMb: float = Field(..., description="static/ 디렉토리가 위치한 파티션의 남은 디스크 공간 (MB 단위)", example=51200.50)
    gpu: Optional[GpuStatus] = Field(None, description="GPU 상태 정보. NVIDIA GPU가 없거나 nvidia-smi 실행 실패 시 null")
    timestamp: int = Field(..., description="상태 조회 시점의 Unix 타임스탬프 (초 단위)", example=1716134400)


class JobAcceptedResponse(BaseModel):
    """비동기 작업이 수락되었을 때 반환되는 응답 모델입니다. 클라이언트는 이 taskId로 웹훅 결과를 매칭합니다."""
    taskId: str = Field(
        ...,
        description="생성된 고유 작업 ID (UUID v4 형식). 이 ID는 작업 완료 시 웹훅 콜백의 `taskId` 필드와 동일합니다.",
        example="a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    )
    status: str = Field(
        "PROCESSING",
        description="작업 상태. 수락 시 항상 `PROCESSING`. 이후 웹훅으로 `SUCCESS` 또는 `FAILED`가 전달됩니다.",
        example="PROCESSING"
    )
    message: str = Field(
        "Background task started",
        description="작업 수락 안내 메시지",
        example="Background task started"
    )


class ContentResult(BaseModel):
    """마케팅 콘텐츠 생성 완료 시 웹훅으로 전송되는 데이터 모델입니다 (참고용 - 직접 반환되지 않음)."""
    generatedText: str = Field(
        ...,
        description="AI(Qwen2.5)가 생성한 마케팅 홍보 텍스트. 날씨·태그·키워드를 반영하며, 맨 끝에 [IMAGE_PROMPT] 포함",
        example="☀️ 화창한 날씨에 딱 맞는 시원한 아이스 아메리카노!\n오늘 하루도 힘내세요 ☕\n\n[IMAGE_PROMPT]: iced americano on sunny cafe terrace..."
    )
    generatedImageUrl: Optional[str] = Field(
        None,
        description="SDXL로 생성된 포스터 이미지의 접근 URL. 이미지 생성 실패 시 null",
        example="http://localhost:8000/static/images/poster_1716134400.png"
    )
    generatedVideoUrl: Optional[str] = Field(
        None,
        description="FFmpeg로 생성된 숏폼 영상(MP4)의 접근 URL. 영상 생성 실패 시 null",
        example="http://localhost:8000/static/videos/shortform_1716134400.mp4"
    )
    targetTimeSlot: str = Field(..., description="요청 시 지정한 타겟 시간대", example="morning")
    createdAtMillis: int = Field(..., description="콘텐츠 생성 완료 시각 (Unix 밀리초 타임스탬프)", example=1716134400000)


class ShortformResult(BaseModel):
    """숏폼 영상 생성 완료 시 웹훅으로 전송되는 데이터 모델입니다 (참고용 - 직접 반환되지 않음)."""
    videoUrl: str = Field(..., description="생성된 숏폼 영상(MP4) 접근 URL", example="http://localhost:8000/static/videos/shortform_1716134400.mp4")
    imageCount: int = Field(..., description="영상에 사용된 이미지 개수", example=3)
    marketingText: str = Field(..., description="영상에 자막으로 표시된 마케팅 문구", example="맛있는 커피 한 잔의 여유")
    durationSeconds: float = Field(..., description="생성된 영상의 총 재생 시간 (초)", example=7.6)
    createdAtMillis: int = Field(..., description="영상 생성 완료 시각 (Unix 밀리초 타임스탬프)", example=1716134400000)


class WeatherInfo(BaseModel):
    """실시간 날씨 정보 응답 모델입니다. Open-Meteo API에서 가져온 데이터를 가공하여 반환합니다."""
    weatherDesc: str = Field(
        ...,
        description="날씨 상태 한글 설명. 가능한 값: `맑음`, `구름 조금/흐림`, `안개`, `비`, `눈`, `뇌우/폭풍`",
        example="맑음"
    )
    temperature: Optional[float] = Field(None, description="현재 기온 (섭씨 °C)", example=23.5)
    visualCue: str = Field(
        ...,
        description="이미지 생성 프롬프트에 삽입할 시각적 분위기 힌트 (영어). 날씨에 따라 자동 결정됩니다.",
        example="sunny, bright, clear sky, vibrant"
    )
    weatherCode: Optional[int] = Field(
        None,
        description="WMO 기상 코드. 0=맑음, 1~3=구름, 45/48=안개, 51~82=비/이슬비, 71~86=눈, 95~99=뇌우. "
                    "상세: https://open-meteo.com/en/docs#weathervariables",
        example=0
    )
    precipitation: Optional[float] = Field(None, description="현재 강수량 (mm). 0이면 비/눈 없음", example=0.0)
    humidity: Optional[int] = Field(None, description="상대 습도 (0~100%)", example=55)
    windSpeed: Optional[float] = Field(None, description="지상 10m 풍속 (m/s)", example=3.2)
    apparentTemperature: Optional[float] = Field(None, description="체감 온도 (섭씨 °C). 풍속·습도를 고려한 값", example=22.1)
    isDay: Optional[int] = Field(None, description="주간 여부. 1=낮(일출~일몰), 0=밤", example=1)


def fetch_weather_data(lat: float, lon: float) -> dict:
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,wind_speed_10m"
    )
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    data = response.json()
    
    current = data.get("current", {})
    temp = current.get("temperature_2m")
    weather_code = current.get("weather_code")
    precipitation = current.get("precipitation")
    humidity = current.get("relative_humidity_2m")
    wind_speed = current.get("wind_speed_10m")
    apparent_temp = current.get("apparent_temperature")
    is_day = current.get("is_day")
    
    weather_desc = "맑음"
    visual_cue = "sunny, bright, clear sky, vibrant"
    
    if weather_code in [1, 2, 3]:
        weather_desc = "구름 조금/흐림"
        visual_cue = "cloudy, soft lighting, overcast"
    elif weather_code in [45, 48]:
        weather_desc = "안개"
        visual_cue = "foggy, mysterious, misty, muted colors"
    elif weather_code in [51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82]:
        weather_desc = "비"
        visual_cue = "rainy, wet streets, puddles, cinematic moody lighting, water drops"
    elif weather_code in [71, 73, 75, 77, 85, 86]:
        weather_desc = "눈"
        visual_cue = "snowy, winter wonderland, falling snow, cold, cozy"
    elif weather_code in [95, 96, 99]:
        weather_desc = "뇌우/폭풍"
        visual_cue = "stormy, lightning, dark dramatic clouds, heavy rain"
        
    return {
        "weatherDesc": weather_desc,
        "temperature": temp,
        "visualCue": visual_cue,
        "weatherCode": weather_code,
        "precipitation": precipitation,
        "humidity": humidity,
        "windSpeed": wind_speed,
        "apparentTemperature": apparent_temp,
        "isDay": is_day
    }


def get_weather_context(lat: Optional[float], lon: Optional[float]) -> str:
    if lat is None or lon is None:
        return "날씨 정보 없음 (기본 설정: 맑음, 기온 20도). 밝고 긍정적인 분위기로 작성해주세요."
    
    try:
        w = fetch_weather_data(lat, lon)
        is_day_str = "낮" if w.get("isDay") == 1 else "밤"
        
        context = (
            f"현재 위치의 날씨는 '{w['weatherDesc']}'이며, 기온은 {w['temperature']}도(체감 {w['apparentTemperature']}도)입니다. "
            f"현재 시간대는 {is_day_str}이며, 습도는 {w['humidity']}%, 풍속은 {w['windSpeed']}m/s입니다. "
        )
        
        if w.get("precipitation", 0) > 0:
            context += f"현재 강수량은 {w['precipitation']}mm로 비나 눈이 내리고 있습니다. "
        
        context += f"이미지 프롬프트 작성 시 시각적 분위기 힌트({w['visualCue']})를 적극 활용하여 현장감 있는 홍보물을 만드세요."
        
        return context
    except Exception as e:
        print(f"Weather API error: {e}")
        return "날씨 정보 조회 실패 (기본 설정: 맑음). 밝고 긍정적인 분위기로 작성해주세요."


@app.get(
    "/api/weather",
    response_model=WeatherInfo,
    tags=["🌤️ 날씨 정보"],
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
):
    try:
        w = fetch_weather_data(lat, lon)
        return WeatherInfo(**w)
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=f"날씨 정보를 가져오는데 실패했습니다: {str(e)}")


def get_gpu_status() -> Optional[dict]:
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total", "--format=csv,noheader,nounits"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2
        )
        if res.returncode == 0:
            lines = res.stdout.strip().split("\n")
            if lines:
                parts = [p.strip() for p in lines[0].split(",")]
                if len(parts) >= 4:
                    return {
                        "gpuUtil": int(parts[0]),
                        "gpuMemUtil": int(parts[1]),
                        "gpuMemUsedMb": int(parts[2]),
                        "gpuMemTotalMb": int(parts[3])
                    }
    except Exception as e:
        print(f"Failed to get GPU status: {e}")
    return None


@app.get(
    "/api/system/status",
    response_model=SystemStatusResponse,
    tags=["🖥️ 시스템 모니터링"],
    summary="서버 시스템 상태 조회 (Health Check)",
    description="""
서버의 현재 상태를 조회합니다. Spring 백엔드에서 **무거운 AI 생성 작업을 요청하기 전에** 이 API를 호출하여 서버가 작업을 수락할 수 있는 상태인지 확인해야 합니다.

### 반환되는 상태값
| status | 의미 | 권장 행동 |
|--------|------|--------|
| `available` | 서버가 새 작업을 수락할 수 있음 | 콘텐츠 생성 요청 가능 |
| `busy` | 서버가 과부하 상태 | 요청을 잠시 뒤로 미루기 |

### busy 판정 기준 (하나라도 해당 시)
1. 현재 실행 중인 작업 수 ≥ 최대 동시 작업 수 (2개)
2. 디스크 여유 공간 ≤ 1,000MB
3. GPU 사용률 ≥ 90%

### 파라미터
이 API는 파라미터가 없습니다. 호출하면 즉시 현재 상태를 반환합니다.
""",
    responses={
        200: {"description": "시스템 상태 조회 성공"},
    },
)
async def get_system_status():
    global active_jobs_count
    
    disk_usage = shutil.disk_usage("static/")
    free_mb = disk_usage.free / (1024 * 1024)
    
    gpu_status = get_gpu_status()
    gpu_busy = gpu_status is not None and gpu_status["gpuUtil"] >= 90
    
    if active_jobs_count >= MAX_CONCURRENT_JOBS or free_mb <= 1000 or gpu_busy:
        current_status = "busy"
    else:
        current_status = "available"
        
    return SystemStatusResponse(
        status=current_status,
        activeJobs=active_jobs_count,
        maxConcurrentJobs=MAX_CONCURRENT_JOBS,
        diskSpaceFreeMb=round(free_mb, 2),
        gpu=gpu_status,
        timestamp=int(time.time())
    )


@app.get(
    "/",
    response_class=HTMLResponse,
    tags=["🏠 홈"],
    summary="서버 접속 확인 테스트 페이지",
    description="브라우저에서 이 URL에 접속하면 서버가 정상 실행 중인지 확인할 수 있는 HTML 페이지가 표시됩니다. API 기능과는 무관한 순수 테스트용 페이지입니다.",
)
async def home() -> HTMLResponse:
        return HTMLResponse(
                content="""
                <!doctype html>
                <html lang="ko">
                <head>
                    <meta charset="utf-8" />
                    <meta name="viewport" content="width=device-width, initial-scale=1" />
                    <title>Marketing AI Agent Test Page</title>
                    <style>
                        :root {
                            color-scheme: light;
                            --bg: #0f172a;
                            --panel: rgba(15, 23, 42, 0.86);
                            --text: #e2e8f0;
                            --muted: #94a3b8;
                            --accent: #fbbf24;
                            --accent-2: #22c55e;
                        }
                        * { box-sizing: border-box; }
                        body {
                            margin: 0;
                            min-height: 100vh;
                            display: grid;
                            place-items: center;
                            font-family: Arial, Helvetica, sans-serif;
                            color: var(--text);
                            background:
                                radial-gradient(circle at top, rgba(251, 191, 36, 0.25), transparent 30%),
                                radial-gradient(circle at bottom right, rgba(34, 197, 94, 0.18), transparent 24%),
                                linear-gradient(135deg, #020617, #0f172a 55%, #111827);
                        }
                        .card {
                            width: min(720px, calc(100vw - 32px));
                            padding: 32px;
                            border: 1px solid rgba(148, 163, 184, 0.22);
                            border-radius: 20px;
                            background: var(--panel);
                            box-shadow: 0 24px 80px rgba(2, 6, 23, 0.5);
                        }
                        .badge {
                            display: inline-block;
                            padding: 6px 12px;
                            border-radius: 999px;
                            background: rgba(251, 191, 36, 0.12);
                            color: var(--accent);
                            font-size: 14px;
                            font-weight: 700;
                            letter-spacing: 0.02em;
                        }
                        h1 {
                            margin: 16px 0 12px;
                            font-size: clamp(32px, 6vw, 56px);
                            line-height: 1.05;
                        }
                        p {
                            margin: 0 0 14px;
                            color: var(--muted);
                            font-size: 16px;
                            line-height: 1.7;
                        }
                        .meta {
                            display: grid;
                            gap: 10px;
                            margin-top: 24px;
                            padding-top: 20px;
                            border-top: 1px solid rgba(148, 163, 184, 0.16);
                            font-size: 15px;
                        }
                        .meta code {
                            color: #fff;
                            background: rgba(15, 23, 42, 0.8);
                            padding: 2px 8px;
                            border-radius: 8px;
                        }
                        a {
                            color: var(--accent);
                            text-decoration: none;
                        }
                        .status {
                            display: inline-flex;
                            align-items: center;
                            gap: 8px;
                            margin-top: 18px;
                            color: #d1fae5;
                            font-weight: 600;
                        }
                        .dot {
                            width: 10px;
                            height: 10px;
                            border-radius: 50%;
                            background: var(--accent-2);
                            box-shadow: 0 0 0 6px rgba(34, 197, 94, 0.12);
                        }
                    </style>
                </head>
                <body>
                    <main class="card">
                        <span class="badge">External test page</span>
                        <h1>FastAPI 서버 접속 확인 페이지</h1>
                        <p>이 페이지가 보이면 서버가 실행 중이고, HTTP 접속이 가능한 상태입니다.</p>
                        <p>브라우저에서 이 주소를 열어 확인하세요. 같은 네트워크 밖에서 접속하려면 포트 포워딩이나 터널이 추가로 필요합니다.</p>
                        <div class="status"><span class="dot"></span>Server is reachable</div>
                        <div class="meta">
                            <div>테스트 엔드포인트: <code>/</code></div>
                            <div>API 문서: <a href="/docs">/docs</a></div>
                            <div>정적 파일: <a href="/static">/static</a></div>
                        </div>
                    </main>
                </body>
                </html>
                """
        )


# --- WEBHOOK URL Configuration ---
SPRING_WEBHOOK_URL = os.environ.get("SPRING_WEBHOOK_URL", "http://localhost:8080/api/internal/content-callback")

async def worker_generate_content(
    task_id: str,
    base_url: str,
    time_slot: str,
    tag_list: List[str],
    keyword_list: List[str],
    weather_data: str,
    top_performers_context: str = ""
):
    global active_jobs_count
    active_jobs_count += 1
    try:
        # 2. Setup LangChain with Ollama (qwen2.5:3b)
        chat_model = ChatOllama(
            model="qwen2.5:3b",
            temperature=0.7,
            base_url="http://localhost:11434"
        )
        
        # 3. Create Prompt
        performers_section = ""
        if top_performers_context:
            performers_section = f"""
[과거 우수 성과 게시물 레퍼런스]
{top_performers_context}

아래의 [과거 우수 성과 게시물 레퍼런스]는 우리 매장에서 반응이 가장 좋았던 홍보물들입니다. 이 텍스트들의 문체, 감성, 길이를 분석하고 모방하여 이번 타겟 시간대와 날씨에 맞는 새로운 홍보 텍스트를 작성해 주세요.
"""
        prompt_text = f"""
당신은 소상공인을 돕는 전문 마케터입니다. 아래 정보를 바탕으로 매력적인 홍보 텍스트를 작성하고, 
맨 마지막 줄에 포스터 이미지를 만들기 위한 [IMAGE_PROMPT]: (영어 프롬프트) 를 작성해주세요.

[실시간 날씨 컨텍스트]
{{weather_data}}

[마케팅 정보]
- 타겟 시간대: {{time_slot}}
- 태그: {{tags}}
- 키워드: {{keywords}}
{performers_section}
중요 지시사항:
1. 홍보 텍스트는 [실시간 날씨 컨텍스트]의 기상 상황(맑음, 비, 눈 등)을 자연스럽게 반영하여 작성하세요.
2. [IMAGE_PROMPT] 작성 시, 날씨에 어울리는 시각적 분위기(visual cue)를 프롬프트에 반드시 포함하여 날씨가 반영된 고품질 이미지가 생성되도록 하세요.

출력 형식:
(홍보 텍스트 내용)

[IMAGE_PROMPT]: (English description for image generation)
        """
        prompt_template = PromptTemplate.from_template(prompt_text)
        
        chain = prompt_template | chat_model | StrOutputParser()
        
        # 4. Generate Text using local Ollama
        print(f"[{task_id}] Generating text via Ollama (qwen2.5:3b)...")
        result_text = chain.invoke({
            "weather_data": weather_data,
            "time_slot": time_slot,
            "tags": ", ".join(tag_list),
            "keywords": ", ".join(keyword_list)
        })
        
        # 5. Extract Image Prompt and Generate Image via Local SDXL
        generated_image_url = None
        generated_image_filename = None
        
        if "[IMAGE_PROMPT]:" in result_text:
            parts = result_text.split("[IMAGE_PROMPT]:")
            image_prompt = parts[1].strip()
            
            print(f"[{task_id}] Generating poster image via local SDXL...")
            try:
                generated_image_filename = await run_in_threadpool(
                    image_generator.generate_image,
                    image_prompt
                )
                generated_image_url = f"{base_url}/static/images/{generated_image_filename}"
            except Exception as e:
                print(f"[{task_id}] Image generation failed: {e}")
                    
        # 6. Video Generation Pipeline (FFmpeg 기반 - Ken Burns + 텍스트 오버레이)
        generated_video_url = None
        if generated_image_filename:
            print(f"[{task_id}] Starting video generation via FFmpeg...")
            try:
                # 로컬 이미지 파일 경로를 직접 전달
                local_image_path = os.path.join("static/images", generated_image_filename)
                clean_text = result_text.split("[IMAGE_PROMPT]:")[0].strip() if "[IMAGE_PROMPT]:" in result_text else result_text
                filename = await run_in_threadpool(
                    video_generator.generate_video_from_local,
                    local_image_path,
                    clean_text
                )
                generated_video_url = f"{base_url}/static/videos/{filename}"
            except Exception as e:
                print(f"[{task_id}] Video generation failed: {e}")
        
        # Success Webhook Payload
        payload = {
            "taskId": task_id,
            "status": "SUCCESS",
            "jobType": "GENERATE_CONTENT",
            "data": {
                "generatedText": result_text,
                "generatedImageUrl": generated_image_url,
                "generatedVideoUrl": generated_video_url,
                "targetTimeSlot": time_slot,
                "createdAtMillis": int(time.time() * 1000)
            }
        }
        print(f"[{task_id}] Task completed successfully. Sending webhook to {SPRING_WEBHOOK_URL}...")
        try:
            requests.post(SPRING_WEBHOOK_URL, json=payload, timeout=5)
        except Exception as we:
            print(f"[{task_id}] Webhook send failed: {we}")

    except Exception as e:
        print(f"[{task_id}] Task failed: {e}")
        # Error Webhook Payload
        payload = {
            "taskId": task_id,
            "status": "FAILED",
            "jobType": "GENERATE_CONTENT",
            "error": str(e)
        }
        try:
            requests.post(SPRING_WEBHOOK_URL, json=payload, timeout=5)
        except Exception as we:
            print(f"[{task_id}] Webhook send failed: {we}")
    finally:
        active_jobs_count -= 1


@app.post(
    "/api/marketing/generate",
    response_model=JobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["🖼️ 마케팅 콘텐츠 생성"],
    summary="AI 마케팅 콘텐츠 생성 (텍스트 + 포스터 + 영상)",
    description="""
태그, 키워드, 타겟 시간대를 기반으로 **AI가 마케팅 홍보 텍스트 → 포스터 이미지 → 숏폼 영상**을 순차적으로 생성합니다.

### ⚡ 비동기 처리
이 API는 **즉시 202 응답**과 `taskId`를 반환합니다. 실제 AI 생성 작업은 백그라운드에서 진행되며, 완료 시 환경 변수 `SPRING_WEBHOOK_URL`에 설정된 URL로 결과를 POST 전송합니다.

### 🔄 내부 파이프라인
```
1. Qwen2.5:3b (LLM) → 마케팅 텍스트 + 이미지 프롬프트 생성
2. SDXL (GPU) → 768×1344 세로 포스터 이미지 생성
3. FFmpeg → Ken Burns 효과 + 자막 오버레이 숏폼 영상 생성
```

### 📬 웹훅 콜백 형식
작업 완료 시 다음 JSON이 Spring 백엔드로 전송됩니다:
```json
{
  "taskId": "a1b2c3d4-...",
  "status": "SUCCESS",
  "jobType": "GENERATE_CONTENT",
  "data": {
    "generatedText": "AI가 생성한 홍보 텍스트...",
    "generatedImageUrl": "http://host/static/images/poster_xxx.png",
    "generatedVideoUrl": "http://host/static/videos/shortform_xxx.mp4",
    "targetTimeSlot": "morning",
    "createdAtMillis": 1716134400000
  }
}
```
실패 시: `{"taskId": "...", "status": "FAILED", "jobType": "GENERATE_CONTENT", "error": "에러 메시지"}`

### ⚠️ 주의사항
- 작업 시간: GPU 성능에 따라 약 1~5분 소요
- 서버 상태를 먼저 `/api/system/status`로 확인한 후 요청하세요.
""",
    responses={
        202: {"description": "작업이 수락되어 백그라운드에서 처리 중. 반환된 taskId로 웹훅 결과를 매칭하세요."},
    },
)
async def generate_content(
    request: Request,
    background_tasks: BackgroundTasks,
    tags: str = Form(
        ...,
        description="마케팅 태그 목록 (쉼표로 구분). 업종·상품 카테고리를 나타냅니다.",
        example="카페,아메리카노,디저트",
    ),
    keywords: str = Form(
        ...,
        description="마케팅 키워드 목록 (쉼표로 구분). 강조하고 싶은 핵심 문구입니다.",
        example="시원한,신메뉴,할인",
    ),
    timeSlot: str = Form(
        ...,
        description="타겟 시간대. LLM이 이 시간대에 맞는 톤으로 텍스트를 생성합니다. 예: morning, lunch, afternoon, evening, night",
        example="morning",
    ),
    image: Optional[UploadFile] = File(
        None,
        description="(선택) 참고용 이미지 파일. 현재 버전에서는 사용되지 않지만, 향후 이미지 분석 연동을 위해 예약된 필드입니다.",
    ),
    lat: Optional[float] = Form(
        None,
        description="(선택) 매장 위치의 위도. 입력 시 실시간 날씨를 반영한 콘텐츠를 생성합니다. 미입력 시 기본 맑은 날씨로 처리됩니다.",
        example=35.8714,
    ),
    lon: Optional[float] = Form(
        None,
        description="(선택) 매장 위치의 경도. lat과 함께 입력해야 날씨 정보가 반영됩니다.",
        example=128.6014,
    ),
    topPerformers: Optional[str] = Form(
        None,
        description="""
(선택) 과거 우수 성과 게시물 데이터 (JSON 문자열). Spring 백엔드에서 클릭 수 기준 상위 게시물을 전달하면, AI가 해당 문체와 감성을 참고하여 새 콘텐츠를 생성합니다.

**JSON 형식 예시:**
```json
[
  {"clickCount": 150, "marketingText": "오늘 하루도 커피 한 잔의 여유!", "tags": ["카페", "아메리카노"]},
  {"clickCount": 120, "marketingText": "비 오는 날 따뜻한 라떼 어때요?", "tags": ["카페", "라떼"]}
]
```
""",
    ),
):
    task_id = str(uuid.uuid4())
    tag_list = [t.strip() for t in tags.split(",")]
    keyword_list = [k.strip() for k in keywords.split(",")]
    weather_data = get_weather_context(lat, lon)
    base_url = str(request.base_url).rstrip("/")
    
    top_performers_context = ""
    if topPerformers:
        try:
            performers_list = json.loads(topPerformers)
            if isinstance(performers_list, list) and performers_list:
                context_parts = []
                for i, p in enumerate(performers_list, 1):
                    click_count = p.get("clickCount", 0)
                    marketing_text = p.get("marketingText", "")
                    p_tags = p.get("tags", [])
                    tag_str = ", ".join(p_tags)
                    context_parts.append(
                        f"우수사례 {i} (클릭수: {click_count}) - 내용: {marketing_text} / 태그: {tag_str}"
                    )
                top_performers_context = "\n".join(context_parts)
        except Exception as e:
            print(f"[{task_id}] Failed to parse topPerformers: {e}")
            top_performers_context = ""
            
    background_tasks.add_task(
        worker_generate_content,
        task_id=task_id,
        base_url=base_url,
        time_slot=timeSlot,
        tag_list=tag_list,
        keyword_list=keyword_list,
        weather_data=weather_data,
        top_performers_context=top_performers_context
    )
    
    return JobAcceptedResponse(taskId=task_id)

async def worker_create_shortform(
    task_id: str,
    base_url: str,
    saved_paths: List[str],
    text: str,
    seconds_per_image: float
):
    global active_jobs_count
    active_jobs_count += 1
    try:
        print(f"[{task_id}] Creating shortform video from {len(saved_paths)} images...")
        td = 0.7 if len(saved_paths) > 1 else 0.0
        try:
            filename = await run_in_threadpool(
                video_generator.create_shortform_video,
                saved_paths,
                text,
                "static/videos",
                seconds_per_image,
                td,
            )
        finally:
            # 업로드 임시 파일 정리
            for p in saved_paths:
                if os.path.exists(p):
                    os.remove(p)

        # 실제 생성된 비디오 길이를 ffprobe로 정확하게 조회
        try:
            video_full_path = os.path.join("static/videos", filename)
            probe_cmd = [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", video_full_path
            ]
            probe_res = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            total_dur = round(float(probe_res.stdout.strip()), 2)
        except Exception as e:
            print(f"[{task_id}] Failed to probe video duration: {e}. Using fallback calculation.")
            total_dur = round(n * seconds_per_image - max(0, n - 1) * td, 2)

        generated_video_url = f"{base_url}/static/videos/{filename}"
        
        # Success Webhook Payload
        payload = {
            "taskId": task_id,
            "status": "SUCCESS",
            "jobType": "CREATE_SHORTFORM",
            "data": {
                "videoUrl": generated_video_url,
                "imageCount": n,
                "marketingText": text,
                "durationSeconds": total_dur,
                "createdAtMillis": int(time.time() * 1000)
            }
        }
        print(f"[{task_id}] Task completed successfully. Sending webhook to {SPRING_WEBHOOK_URL}...")
        try:
            requests.post(SPRING_WEBHOOK_URL, json=payload, timeout=5)
        except Exception as we:
            print(f"[{task_id}] Webhook send failed: {we}")
            
    except Exception as e:
        print(f"[{task_id}] Task failed: {e}")
        # Error Webhook Payload
        payload = {
            "taskId": task_id,
            "status": "FAILED",
            "jobType": "CREATE_SHORTFORM",
            "error": str(e)
        }
        try:
            requests.post(SPRING_WEBHOOK_URL, json=payload, timeout=5)
        except Exception as we:
            print(f"[{task_id}] Webhook send failed: {we}")
    finally:
        active_jobs_count -= 1


@app.post(
    "/api/marketing/create-shortform",
    response_model=JobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["🖼️ 마케팅 콘텐츠 생성"],
    summary="사용자 이미지 기반 숏폼 영상 생성",
    description="""
사용자가 직접 업로드한 **1~5장의 이미지**와 마케팅 문구로 프로페셔널 숏폼 영상(Instagram Reels / YouTube Shorts 품질)을 생성합니다.

### ⚡ 비동기 처리
`/api/marketing/generate`와 동일하게 즉시 202 응답 + `taskId`를 반환하고, 완료 시 웹훅으로 결과를 전송합니다.

### 🎬 영상 제작 파이프라인
```
1. 이미지 전처리: 1080×1920 (9:16) 리사이즈 + 중앙 크롭
2. Ken Burns 효과: 각 이미지에 줌인/줌아웃/패닝 중 랜덤 적용
3. 트랜지션: fade, slide, wipe, circlecrop 등 광고 스타일 전환
4. 자막 오버레이: PIL로 한글 대형 자막 + 그라데이션 배경 렌더링
5. TTS 음성: edge-tts로 한국어 나레이션 자동 생성
6. BGM 믹싱: static/bgm/ 폴더의 배경 음악 랜덤 선택
7. 시네마틱 필터: 색감 보정 + 선명도 + 비네팅 효과
```

### 📐 출력 사양
- 해상도: **1080×1920** (세로 9:16)
- 코덱: H.264 (libx264)
- 포맷: MP4 (faststart)

### 📬 웹훅 콜백 형식
```json
{
  "taskId": "a1b2c3d4-...",
  "status": "SUCCESS",
  "jobType": "CREATE_SHORTFORM",
  "data": {
    "videoUrl": "http://host/static/videos/shortform_xxx.mp4",
    "imageCount": 3,
    "marketingText": "입력한 마케팅 문구",
    "durationSeconds": 7.6,
    "createdAtMillis": 1716134400000
  }
}
```
""",
    responses={
        202: {"description": "숏폼 영상 생성 작업이 수락됨. 백그라운드에서 처리 후 웹훅으로 결과 전송"},
        400: {"description": "이미지 개수가 1~5개 범위를 벗어남"},
    },
)
async def create_shortform(
    request: Request,
    background_tasks: BackgroundTasks,
    images: List[UploadFile] = File(
        ...,
        description="숏폼 영상에 사용할 이미지 파일들 (최소 1개, 최대 5개). 지원 형식: JPG, PNG, WebP. 여러 장 업로드 시 순서대로 트랜지션이 적용됩니다.",
    ),
    text: str = Form(
        ...,
        description="영상 하단에 자막으로 표시될 마케팅 문구. 한 줄 14글자, 최대 4줄까지 표시됩니다. TTS 나레이션의 원본 텍스트로도 사용됩니다.",
        example="오늘의 특별한 커피 한 잔으로\n하루를 시작해보세요!",
    ),
    secondsPerImage: float = Form(
        3.0,
        description="이미지당 화면에 표시되는 시간 (초). 기본값 3.0초. 이미지가 여러 장이면 트랜지션 시간(0.7초)이 겹치므로, 총 영상 길이 = (이미지 수 × 이 값) - ((이미지 수 - 1) × 0.7초)",
        example=3.0,
        ge=1.0,
        le=10.0,
    ),
):
    if len(images) < 1 or len(images) > 5:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="이미지는 1~5개까지 업로드할 수 있습니다.")

    task_id = str(uuid.uuid4())
    upload_dir = "static/uploads"
    os.makedirs(upload_dir, exist_ok=True)
    
    saved_paths = []
    for i, img in enumerate(images):
        ext = os.path.splitext(img.filename or "img.png")[1] or ".png"
        path = os.path.join(upload_dir, f"upload_{task_id}_{i}{ext}")
        content = await img.read()
        with open(path, "wb") as f:
            f.write(content)
        saved_paths.append(path)

    base_url = str(request.base_url).rstrip("/")
    
    background_tasks.add_task(
        worker_create_shortform,
        task_id=task_id,
        base_url=base_url,
        saved_paths=saved_paths,
        text=text,
        seconds_per_image=secondsPerImage
    )

    return JobAcceptedResponse(taskId=task_id)

@app.post(
    "/api/vision/analyze",
    tags=["👁️ 이미지 분석 (Vision)"],
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
async def analyze_image_standalone(
    image: UploadFile = File(
        ...,
        description="분석할 이미지 파일. 지원 형식: JPG, PNG, WebP, GIF 등 일반 이미지 형식. Content-Type이 `image/`로 시작해야 합니다.",
    ),
):
    if not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="이미지 파일만 업로드 가능합니다.")
        
    try:
        # 파일 읽기
        image_bytes = await image.read()
        
        # 비동기 환경에서 동기 함수를 블로킹 없이 실행
        analysis_result = await run_in_threadpool(
            vision_analyzer.analyze_image_for_marketing,
            image_bytes
        )
        
        return {
            "success": True,
            "filename": image.filename,
            "analysis": analysis_result
        }
    except Exception as e:
        print(f"이미지 분석 중 오류 발생: {e}")
        raise HTTPException(status_code=500, detail="이미지 분석에 실패했습니다.")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
