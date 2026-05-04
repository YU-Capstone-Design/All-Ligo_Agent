import os
import time
from typing import Optional, List
from fastapi import FastAPI, Form, UploadFile, File, Request
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


def get_weather_context() -> str:
    # [MOCK] Weather API
    return "오늘 날씨는 화창하고 기온은 24도입니다. 야외 활동이나 시원한 음료를 홍보하기에 최적의 날씨입니다."


@app.post("/api/marketing/generate", response_model=ContentResult)
async def generate_content(
    request: Request,
    tags: str = Form(...),
    keywords: str = Form(...),
    timeSlot: str = Form(...),
    image: Optional[UploadFile] = File(None)
):
    # 1. Prepare inputs
    tag_list = [t.strip() for t in tags.split(",")]
    keyword_list = [k.strip() for k in keywords.split(",")]
    weather_data = get_weather_context()
    
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
