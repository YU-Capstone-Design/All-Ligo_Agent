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
import s3_uploader

app = FastAPI(
    title="📢 All-Ligo 마케팅 AI 에이전트 API",
    description="""
## 소상공인을 위한 AI 마케팅 콘텐츠 자동 생성 서버

이 API는 **100% 로컬 AI 모델**을 활용하여 소상공인의 마케팅 콘텐츠(텍스트, 포스터 이미지, 숏폼 영상)를 자동으로 생성합니다.

### 🏗️ 시스템 아키텍처
- **텍스트 생성**: Ollama + Gemma4:latest (로컬 LLM)
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
import youtube_uploader

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
    contentType: str = Field(..., description="POST 또는 VIDEO")
    mode: str = Field(..., description="TRANSFORM 또는 ORIGINAL")
    generatedText: str = Field(
        ...,
        description="Gemma4가 생성한 마케팅 텍스트 (블로그 글 또는 영상 자막)"
    )
    posterUrl: Optional[str] = Field(
        None,
        description="AI이미지 또는 원본이미지의 접근 URL. 실패 시 null"
    )
    s3VideoUrl: Optional[str] = Field(
        None,
        description="비디오 생성 시에만 존재, 없으면 null"
    )
    localVideoPath: Optional[str] = Field(
        None,
        description="생성된 비디오의 서버 내 로컬 파일 경로. 없으면 null"
    )
    uploadSchedule: str = Field(..., description="요청 시 지정한 업로드 예약 일정 (요일 + 시간)")
    createdAtMillis: int = Field(..., description="콘텐츠 생성 완료 시각 (Unix 밀리초 타임스탬프)")


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
    gpu_busy = gpu_status is not None and (gpu_status["gpuUtil"] >= 90 or gpu_status["gpuMemUtil"] >= 80)
    
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
    content_type: str,
    mode: str,
    saved_image_path: Optional[str],
    schedule_id: Optional[str],
    mood_tag: str,
    hash_tag: str,
    user_prompt: str,
    upload_day: str,
    upload_time: str,
    weather_data: str,
    top_performers_context: str = ""
):
    global active_jobs_count
    active_jobs_count += 1
    try:
        # Step 1: Image Processing (mode 분기)
        vision_keywords = ""
        generated_image_filenames = []
        generated_image_urls = []
        
        if mode == "TRANSFORM":
            if saved_image_path:
                print(f"[{task_id}] Analyzing uploaded image via LLaVA...")
                with open(saved_image_path, "rb") as f:
                    image_bytes = f.read()
                analysis_result = await run_in_threadpool(
                    vision_analyzer.analyze_image_for_marketing,
                    image_bytes
                )
                vision_keywords = f"\n[업로드 이미지 분석 결과]\n- 주요 객체: {', '.join(analysis_result.get('objects', []))}\n- 분위기: {', '.join(analysis_result.get('mood', []))}\n- 주요 색상: {', '.join(analysis_result.get('colors', []))}\n이 분석 결과를 바탕으로 새로운 마케팅 텍스트와 이미지 프롬프트를 작성하세요."
        elif mode == "ORIGINAL":
            if saved_image_path:
                print(f"[{task_id}] Mode is ORIGINAL. Skipping AI image generation.")
                import shutil
                ext = os.path.splitext(saved_image_path)[1] or ".png"
                filename = f"poster_{task_id}{ext}"
                dest_path = os.path.join("static/images", filename)
                shutil.copy(saved_image_path, dest_path)
                generated_image_filenames.append(filename)
                generated_image_urls.append(f"{base_url}/static/images/{filename}")

        # Step 2: Text Generation (contentType 분기)
        chat_model = ChatOllama(
            model="gemma4:latest",
            temperature=0.7,
            base_url="http://localhost:11434"
        )
        
        performers_section = ""
        if top_performers_context:
            performers_section = f"\n[과거 우수 성과 게시물 레퍼런스]\n{top_performers_context}\n\n아래의 [과거 우수 성과 게시물 레퍼런스]는 우리 매장에서 반응이 가장 좋았던 홍보물들입니다. 이 텍스트들의 문체, 감성, 길이를 분석하고 모방하여 이번 타겟 시간대와 날씨에 맞는 새로운 홍보 텍스트를 작성해 주세요.\n"

        content_type_instruction = ""
        if content_type == "POST":
            content_type_instruction = "블로그나 인스타그램 포스트용이므로, 이모지를 포함하여 3문단 이상의 충분한 길이로 상세한 홍보 글을 작성하세요."
        elif content_type == "VIDEO":
            content_type_instruction = "숏폼 영상의 자막 및 설명란 용도이므로, 띄어쓰기 포함 50자 이내, 짧고 강렬한 1~2문장으로 작성하세요."

        image_prompt_instruction = ""
        if mode == "TRANSFORM":
            image_prompt_instruction = "맨 마지막 줄에 포스터 이미지를 만들기 위한 [IMAGE_PROMPT]: (영어 프롬프트) 를 작성해주세요.\n\n날씨에 어울리는 시각적 분위기(visual cue)와 분위기 태그({mood_tag})의 감성을 반영한 3개의 서로 다른 고품질 이미지 프롬프트를 반드시 영어로 작성하세요."
        else:
            image_prompt_instruction = "이미지 생성은 하지 않으므로 [IMAGE_PROMPT]는 절대 작성하지 마세요."

        prompt_text = f"""당신은 소상공인을 돕는 전문 마케터입니다. 아래 정보를 바탕으로 매력적인 홍보 텍스트를 작성하세요.
{image_prompt_instruction}

[실시간 날씨 컨텍스트]
{{weather_data}}

[마케팅 정보]
- 분위기 태그: {{mood_tag}}
- 해시태그: {{hash_tag}}
- 사용자 추가 요청: {{user_prompt}}
- 업로드 예정 요일: {{upload_day}}
- 업로드 예정 시간: {{upload_time}}{vision_keywords}{performers_section}
중요 지시사항:
1. {content_type_instruction}
2. "내용 :", "마케팅 문구 :" 등 어떠한 메타 텍스트나 접두사도 절대 포함하지 마세요. 오직 실제 사용될 텍스트만 작성하세요.
3. 홍보 텍스트는 [실시간 날씨 컨텍스트]의 기상 상황을 자연스럽게 반영하여 작성하세요.
4. 업로드 예정 시간({{upload_day}} {{upload_time}})에 맞는 타겟 독자 상황을 고려하세요.
5. 해시태그({{hash_tag}})의 키워드를 홍보 텍스트에 자연스럽게 녹여 작성하세요.

출력 형식:
(여기에 순수 홍보 텍스트만 작성)
"""
        if mode == "TRANSFORM":
            prompt_text += """
[IMAGE_PROMPT_1]: (English description for image 1)
[IMAGE_PROMPT_2]: (English description for image 2)
[IMAGE_PROMPT_3]: (English description for image 3)
"""
        
        prompt_template = PromptTemplate.from_template(prompt_text)
        chain = prompt_template | chat_model | StrOutputParser()
        
        print(f"[{task_id}] Generating text via Ollama (gemma4:latest)...")
        result_text = chain.invoke({
            "weather_data": weather_data,
            "mood_tag": mood_tag,
            "hash_tag": hash_tag,
            "user_prompt": user_prompt,
            "upload_day": upload_day,
            "upload_time": upload_time
        })
        
        import re
        if mode == "TRANSFORM":
            image_prompts = re.findall(r'\[IMAGE_PROMPT(?:_\d+)?\]:\s*(.*)', result_text)
            if image_prompts:
                print(f"[{task_id}] Generating {len(image_prompts[:3])} poster images via local SDXL...")
                try:
                    for prompt in image_prompts[:3]:
                        filename = await run_in_threadpool(
                            image_generator.generate_image,
                            prompt.strip()
                        )
                        if filename:
                            generated_image_filenames.append(filename)
                            generated_image_urls.append(f"{base_url}/static/images/{filename}")
                except Exception as e:
                    print(f"[{task_id}] Image generation failed: {e}")
                    
        # 텍스트 정리
        clean_text = re.sub(r'\[IMAGE_PROMPT.*', '', result_text, flags=re.DOTALL).strip()
        clean_text = re.sub(r'^(?:홍보\s*텍스트|마케팅\s*문구|홍보\s*문구|텍스트\s*내용|내용|자막|출력\s*형식|문구)[\s\:\-]*', '', clean_text, flags=re.IGNORECASE).strip()
        clean_text = re.sub(r'(?:마케팅\s*문구\s*:?)$', '', clean_text, flags=re.IGNORECASE).strip()
        clean_text = clean_text.strip('\'" \n')
                    
        # Step 3: Video Rendering (contentType 분기)
        generated_video_url = None
        s3_video_url = None
        local_video_path = None
        
        if content_type == "VIDEO":
            if generated_image_filenames:
                print(f"[{task_id}] Starting video generation via FFmpeg with {len(generated_image_filenames)} images...")
                try:
                    local_image_paths = [os.path.join("static/images", fn) for fn in generated_image_filenames]
                    filename = await run_in_threadpool(
                        video_generator.create_shortform_video,
                        local_image_paths,
                        clean_text
                    )
                    generated_video_url = f"{base_url}/static/videos/{filename}"
                    
                    try:
                        local_video_path = os.path.join("static/videos", filename)
                        print(f"[{task_id}] Uploading generated video to S3...")
                        s3_object_name = f"content/{filename}" 
                        s3_video_url = await run_in_threadpool(
                            s3_uploader.upload_video_to_s3,
                            local_video_path,
                            s3_object_name
                        )
                        print(f"[{task_id}] S3 upload succeeded: {s3_video_url}")
                    except Exception as s3e:
                        print(f"[{task_id}] S3 upload failed: {s3e}")
                except Exception as e:
                    print(f"[{task_id}] Video generation failed: {e}")
            else:
                print(f"[{task_id}] No images available for video generation.")
        elif content_type == "POST":
            print(f"[{task_id}] contentType is POST. Skipping video generation.")
        
        # Success Webhook Payload
        upload_schedule = f"{upload_day} {upload_time}"
        payload = {
            "taskId": task_id,
            "scheduleId": schedule_id,
            "status": "SUCCESS",
            "jobType": "GENERATE_CONTENT",
            "data": {
                "contentType": content_type,
                "mode": mode,
                "generatedText": clean_text,
                "posterUrl": generated_image_urls[0] if generated_image_urls else None,
                "s3VideoUrl": s3_video_url,
                "localVideoPath": local_video_path,
                "uploadSchedule": upload_schedule,
                "createdAtMillis": int(time.time() * 1000)
            }
        }
        print(f"[{task_id}] Task completed successfully. Sending webhook to {SPRING_WEBHOOK_URL}...")
        print(f"[{task_id}] ▶ Webhook Payload (GENERATE_CONTENT / SUCCESS):\n{json.dumps(payload, indent=2, ensure_ascii=False)}")
        try:
            requests.post(SPRING_WEBHOOK_URL, json=payload, timeout=5)
        except Exception as we:
            print(f"[{task_id}] Webhook send failed: {we}")

    except Exception as e:
        print(f"[{task_id}] Task failed: {e}")
        payload = {
            "taskId": task_id,
            "scheduleId": schedule_id,
            "status": "FAILED",
            "jobType": "GENERATE_CONTENT",
            "error": str(e)
        }
        print(f"[{task_id}] ▶ Webhook Payload (GENERATE_CONTENT / FAILED):\n{json.dumps(payload, indent=2, ensure_ascii=False)}")
        try:
            requests.post(SPRING_WEBHOOK_URL, json=payload, timeout=5)
        except Exception as we:
            print(f"[{task_id}] Webhook send failed: {we}")
    finally:
        active_jobs_count -= 1
        if saved_image_path and os.path.exists(saved_image_path):
            try:
                os.remove(saved_image_path)
            except Exception:
                pass


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
        example="unlisted"
    )

class UploadResponse(BaseModel):
    """YouTube 업로드 결과 응답 모델."""
    status: str = Field(..., description="업로드 결과 상태: SUCCESS 또는 FAILED")
    scheduleId: Optional[str] = Field(None, description="요청 시 전달받은 스케줄 ID")
    youtubeUrl: Optional[str] = Field(None, description="업로드된 YouTube 영상 URL")
    error: Optional[str] = Field(None, description="실패 시 에러 메시지")

@app.post(
    "/api/marketing/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_200_OK,
    tags=["🖼️ 마케팅 콘텐츠 생성"],
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
- 400: 비디오 파일이 mp4 형식이 아님
- 500: YouTube API 업로드 실패
"""
)
async def upload_generated_video(request: UploadRequest):
    # 파일 존재 여부 검증
    if not os.path.exists(request.localVideoPath):
        raise HTTPException(
            status_code=404,
            detail=f"해당 로컬 비디오 파일을 찾을 수 없습니다: {request.localVideoPath}"
        )
    
    # 파일 확장자 검증
    if not request.localVideoPath.lower().endswith(".mp4"):
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 비디오 형식입니다. MP4 파일만 업로드 가능합니다. (입력: {request.localVideoPath})"
        )
    
    # 파일 크기 검증 (0바이트 파일 방지)
    file_size = os.path.getsize(request.localVideoPath)
    if file_size == 0:
        raise HTTPException(
            status_code=400,
            detail=f"비디오 파일의 크기가 0입니다. 손상된 파일일 수 있습니다: {request.localVideoPath}"
        )
        
    try:
        import youtube_uploader
        print(f"[Upload] Starting YouTube upload: {request.localVideoPath} (size: {file_size} bytes, scheduleId: {request.scheduleId})")
        youtube_url = await run_in_threadpool(
            youtube_uploader.upload_video,
            request.localVideoPath,
            request.title,
            request.description,
            request.tags
        )
        
        print(f"[Upload] YouTube upload succeeded: {youtube_url}")
        
        # 업로드 성공 후 로컬 파일 삭제 (옵션)
        # try:
        #     os.remove(request.localVideoPath)
        # except Exception as e:
        #     pass
            
        return UploadResponse(
            status="SUCCESS",
            scheduleId=request.scheduleId,
            youtubeUrl=youtube_url
        )
    except Exception as e:
        error_msg = f"YouTube 업로드 실패: {str(e)}"
        print(f"[Upload] {error_msg}")
        raise HTTPException(
            status_code=500,
            detail=error_msg
        )

@app.post(
    "/api/marketing/generate",
    response_model=JobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["🖼️ 마케팅 콘텐츠 생성"],
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
    background_tasks: BackgroundTasks,
    contentType: str = Form(
        ...,
        description="POST(블로그/인스타용 글+이미지) 또는 VIDEO(숏폼 영상)",
        example="POST",
    ),
    mode: str = Form(
        ...,
        description="TRANSFORM(업로드 이미지 분석 후 AI 변형 생성) 또는 ORIGINAL(업로드 이미지 원본 유지)",
        example="TRANSFORM",
    ),
    scheduleId: Optional[str] = Form(
        None,
        description="Spring Boot 스케줄 DB 식별자.",
        example="42",
    ),
    moodTag: str = Form(
        ...,
        description="분위기 태그. 콘텐츠의 감성/톤을 결정합니다.",
        example="밝은, 쾌활한",
    ),
    hashTag: str = Form(
        ...,
        description="해시태그. 마케팅 키워드로 활용됩니다.",
        example="#카페, #할인",
    ),
    prompt: str = Form(
        "",
        description="사용자 추가 프롬프트.",
        example="신메뉴 아이스 라떼를 강조해주세요",
    ),
    uploadDay: str = Form(
        ...,
        description="업로드 예정 요일.",
        example="월요일",
    ),
    uploadTime: str = Form(
        ...,
        description="업로드 예정 시간(HH:mm).",
        example="18:00",
    ),
    image: Optional[UploadFile] = File(
        None,
        description="(선택) 참고용 이미지 파일. mode가 TRANSFORM이면 분석용, ORIGINAL이면 원본으로 사용됩니다.",
    ),
    lat: Optional[float] = Form(
        None,
        description="(선택) 매장 위치의 위도.",
        example=35.8714,
    ),
    lon: Optional[float] = Form(
        None,
        description="(선택) 매장 위치의 경도.",
        example=128.6014,
    ),
    topPerformers: Optional[str] = Form(
        None,
        description="(선택) 과거 우수 성과 게시물 데이터 (JSON 문자열).",
    ),
):
    task_id = str(uuid.uuid4())
    weather_data = get_weather_context(lat, lon)
    base_url = str(request.base_url).rstrip("/")
    
    saved_image_path = None
    if image and image.filename:
        upload_dir = "static/uploads"
        os.makedirs(upload_dir, exist_ok=True)
        ext = os.path.splitext(image.filename)[1] or ".png"
        saved_image_path = os.path.join(upload_dir, f"upload_{task_id}{ext}")
        content_bytes = await image.read()
        with open(saved_image_path, "wb") as f:
            f.write(content_bytes)

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
        content_type=contentType,
        mode=mode,
        saved_image_path=saved_image_path,
        schedule_id=scheduleId,
        mood_tag=moodTag,
        hash_tag=hashTag,
        user_prompt=prompt,
        upload_day=uploadDay,
        upload_time=uploadTime,
        weather_data=weather_data,
        top_performers_context=top_performers_context
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
