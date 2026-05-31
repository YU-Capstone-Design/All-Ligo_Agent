import base64
import requests
from typing import List
from io import BytesIO
from PIL import Image

def encode_image_to_base64(image_bytes: bytes) -> str:
    """이미지 바이트 데이터를 Base64 문자열로 변환합니다. LLaVA 처리 속도 향상을 위해 해상도를 리사이징합니다."""
    try:
        # 이미지를 열고 최대 해상도를 제한 (예: 800x800)
        img = Image.open(BytesIO(image_bytes))
        img.thumbnail((800, 800))
        
        # RGB 모드로 변환 (알파 채널 제거)
        if img.mode != 'RGB':
            img = img.convert('RGB')
            
        # 압축하여 바이트로 변환
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        optimized_bytes = buffer.getvalue()
        return base64.b64encode(optimized_bytes).decode('utf-8')
    except Exception as e:
        print(f"이미지 리사이징 오류 (원본 데이터 사용): {e}")
        return base64.b64encode(image_bytes).decode('utf-8')

def analyze_image_for_marketing(image_bytes: bytes) -> dict:
    """
    Vision-LLM(LLaVA)을 사용하여 이미지를 분석하고 마케팅 키워드를 추출합니다.
    """
    # 1. 이미지 인코딩 및 최적화
    base64_image = encode_image_to_base64(image_bytes)
    
    # 2. Ollama API 직접 호출 (LangChain 의존성 제거로 안정성/성능 극대화)
    url = "http://localhost:11434/api/chat"
    prompt_text = (
        "You are an expert marketing analyst. Look at this image and extract key elements for an Instagram advertisement. "
        "Provide your analysis exactly in this format:\n"
        "Objects: [main objects separated by comma]\n"
        "Mood: [emotional mood and vibe separated by comma]\n"
        "Colors: [dominant colors separated by comma]"
    )
    
    payload = {
        "model": "llava:latest",
        "messages": [
            {
                "role": "user",
                "content": prompt_text,
                "images": [base64_image]
            }
        ],
        "options": {
            "temperature": 0.2,
            "num_ctx": 4096  # 불필요하게 큰 컨텍스트(32768 등) 방지하여 VRAM 점유 및 hang 해결
        },
        "stream": False
    }
    
    # 3. 모델 호출
    print("Vision-LLM(LLaVA)을 통해 이미지 분석 중...")
    try:
        response = requests.post(url, json=payload, timeout=60)
        response.raise_for_status()
        res_json = response.json()
        result_text = res_json.get("message", {}).get("content", "")
    except Exception as e:
        print(f"Ollama API 호출 중 오류 발생: {e}")
        return {
            "objects": [],
            "mood": [],
            "colors": [],
            "error": str(e)
        }
    
    # 4. 결과 파싱 (텍스트를 딕셔너리 형태로 정제)
    analysis_result = {
        "objects": [],
        "mood": [],
        "colors": []
    }
    
    try:
        lines = result_text.split('\n')
        for line in lines:
            if line.startswith("Objects:"):
                analysis_result["objects"] = [x.strip() for x in line.replace("Objects:", "").split(",")]
            elif line.startswith("Mood:"):
                analysis_result["mood"] = [x.strip() for x in line.replace("Mood:", "").split(",")]
            elif line.startswith("Colors:"):
                analysis_result["colors"] = [x.strip() for x in line.replace("Colors:", "").split(",")]
    except Exception as e:
        print(f"결과 파싱 오류: {e}")
        analysis_result["raw_text"] = result_text

    return analysis_result
