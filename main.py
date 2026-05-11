import os
import time
import requests
from typing import Optional, List
from fastapi import FastAPI, Form, UploadFile, File, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from langchain_core.prompts import PromptTemplate
from langchain_ollama import ChatOllama
from langchain_core.output_parsers import StrOutputParser

app = FastAPI(title="Marketing AI Agent API (Python) - 100% Local AI")

os.makedirs("static/videos", exist_ok=True)
os.makedirs("static/images", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

import image_generator
import video_generator


class ContentResult(BaseModel):
    generatedText: str
    generatedImageUrl: Optional[str] = None
    generatedVideoUrl: Optional[str] = None
    targetTimeSlot: str
    createdAtMillis: int


class ShortformResult(BaseModel):
    videoUrl: str
    imageCount: int
    marketingText: str
    durationSeconds: float
    createdAtMillis: int


class WeatherInfo(BaseModel):
    weatherDesc: str
    temperature: Optional[float] = None
    visualCue: str
    weatherCode: Optional[int] = None
    precipitation: Optional[float] = None
    humidity: Optional[int] = None
    windSpeed: Optional[float] = None
    apparentTemperature: Optional[float] = None
    isDay: Optional[int] = None


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


@app.get("/api/weather", response_model=WeatherInfo)
async def get_weather(lat: float, lon: float):
    try:
        w = fetch_weather_data(lat, lon)
        return WeatherInfo(**w)
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=f"날씨 정보를 가져오는데 실패했습니다: {str(e)}")


@app.get("/", response_class=HTMLResponse)
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


@app.post("/api/marketing/generate", response_model=ContentResult)
async def generate_content(
    request: Request,
    tags: str = Form(...),
    keywords: str = Form(...),
    timeSlot: str = Form(...),
    image: Optional[UploadFile] = File(None),
    lat: Optional[float] = Form(None),
    lon: Optional[float] = Form(None)
):
    # 1. Prepare inputs
    tag_list = [t.strip() for t in tags.split(",")]
    keyword_list = [k.strip() for k in keywords.split(",")]
    weather_data = get_weather_context(lat, lon)
    
    # 2. Setup LangChain with Ollama (qwen2.5:3b) - VRAM 절약을 위해 경량 모델 사용
    chat_model = ChatOllama(
        model="qwen2.5:3b",
        temperature=0.7,
        base_url="http://localhost:11434"
    )
    
    # 3. Create Prompt
    prompt_template = PromptTemplate.from_template("""
당신은 소상공인을 돕는 전문 마케터입니다. 아래 정보를 바탕으로 매력적인 홍보 텍스트를 작성하고, 
맨 마지막 줄에 포스터 이미지를 만들기 위한 [IMAGE_PROMPT]: (영어 프롬프트) 를 작성해주세요.

[실시간 날씨 컨텍스트]
{weather_data}

[마케팅 정보]
- 타겟 시간대: {time_slot}
- 태그: {tags}
- 키워드: {keywords}

중요 지시사항:
1. 홍보 텍스트는 [실시간 날씨 컨텍스트]의 기상 상황(맑음, 비, 눈 등)을 자연스럽게 반영하여 작성하세요.
2. [IMAGE_PROMPT] 작성 시, 날씨에 어울리는 시각적 분위기(visual cue)를 프롬프트에 반드시 포함하여 날씨가 반영된 고품질 이미지가 생성되도록 하세요.

출력 형식:
(홍보 텍스트 내용)

[IMAGE_PROMPT]: (English description for image generation)
    """)
    
    chain = prompt_template | chat_model | StrOutputParser()
    
    # 4. Generate Text using local Ollama
    print("Generating text via Ollama (qwen2.5:3b)...")
    result_text = chain.invoke({
        "weather_data": weather_data,
        "time_slot": timeSlot,
        "tags": ", ".join(tag_list),
        "keywords": ", ".join(keyword_list)
    })
    
    # 5. Extract Image Prompt and Generate Image via Local SDXL
    generated_image_url = None
    generated_image_filename = None
    
    if "[IMAGE_PROMPT]:" in result_text:
        parts = result_text.split("[IMAGE_PROMPT]:")
        image_prompt = parts[1].strip()
        
        print("Generating poster image via local SDXL...")
        try:
            generated_image_filename = await run_in_threadpool(
                image_generator.generate_image,
                image_prompt
            )
            base_url = str(request.base_url).rstrip("/")
            generated_image_url = f"{base_url}/static/images/{generated_image_filename}"
        except Exception as e:
            print(f"Image generation failed: {e}")
                
    # 6. Video Generation Pipeline (FFmpeg 기반 - Ken Burns + 텍스트 오버레이)
    generated_video_url = None
    if generated_image_filename:
        print("Starting video generation via FFmpeg...")
        try:
            # 로컬 이미지 파일 경로를 직접 전달
            local_image_path = os.path.join("static/images", generated_image_filename)
            clean_text = result_text.split("[IMAGE_PROMPT]:")[0].strip() if "[IMAGE_PROMPT]:" in result_text else result_text
            filename = await run_in_threadpool(
                video_generator.generate_video_from_local,
                local_image_path,
                clean_text
            )
            base_url = str(request.base_url).rstrip("/")
            generated_video_url = f"{base_url}/static/videos/{filename}"
        except Exception as e:
            print(f"Video generation failed: {e}")
    
    return ContentResult(
        generatedText=result_text,
        generatedImageUrl=generated_image_url,
        generatedVideoUrl=generated_video_url,
        targetTimeSlot=timeSlot,
        createdAtMillis=int(time.time() * 1000)
    )

@app.post("/api/marketing/create-shortform", response_model=ShortformResult)
async def create_shortform(
    request: Request,
    images: List[UploadFile] = File(...),
    text: str = Form(...),
    secondsPerImage: float = Form(3.0),
):
    """
    사용자가 업로드한 1~5장의 사진으로 프로페셔널 숏폼 영상을 생성합니다.
    - images: 1~5개 이미지 파일
    - text: 마케팅 문구 (영상에 자막으로 표시됨)
    - secondsPerImage: 이미지당 표시 시간 (기본 3초)
    """
    if len(images) < 1 or len(images) > 5:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="이미지는 1~5개까지 업로드할 수 있습니다.")

    # 1. 업로드된 이미지 저장
    upload_dir = "static/uploads"
    os.makedirs(upload_dir, exist_ok=True)
    saved_paths = []
    for i, img in enumerate(images):
        ext = os.path.splitext(img.filename or "img.png")[1] or ".png"
        path = os.path.join(upload_dir, f"upload_{int(time.time())}_{i}{ext}")
        content = await img.read()
        with open(path, "wb") as f:
            f.write(content)
        saved_paths.append(path)

    # 2. 숏폼 영상 생성
    print(f"Creating shortform video from {len(saved_paths)} images...")
    td = 0.7 if len(saved_paths) > 1 else 0.0
    try:
        filename = await run_in_threadpool(
            video_generator.create_shortform_video,
            saved_paths,
            text,
            "static/videos",
            secondsPerImage,
            td,
        )
    finally:
        # 업로드 임시 파일 정리
        for p in saved_paths:
            if os.path.exists(p):
                os.remove(p)

    n = len(saved_paths)
    total_dur = round(n * secondsPerImage - max(0, n - 1) * td, 2)
    base_url = str(request.base_url).rstrip("/")

    return ShortformResult(
        videoUrl=f"{base_url}/static/videos/{filename}",
        imageCount=n,
        marketingText=text,
        durationSeconds=total_dur,
        createdAtMillis=int(time.time() * 1000),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
