import os
import torch
from diffusers import StableDiffusionXLPipeline
from PIL import Image
import time

# 전역 변수로 파이프라인 유지 (한 번만 로드하기 위함)
_pipeline = None

# 로컬 SDXL 기반 이미지 생성 모듈
# 외부 API(OpenAI/DALL-E) 없이 RTX 4080에서 고품질 포스터 이미지를 생성합니다.
# 모델: stabilityai/stable-diffusion-xl-base-1.0 (fp16)


def get_pipeline():
    """SDXL 파이프라인을 로드합니다. 최초 호출 시에만 모델을 다운로드합니다."""
    global _pipeline
    if _pipeline is None:
        print("Loading SDXL Model... This might take a while on the first run.")
        _pipeline = StableDiffusionXLPipeline.from_pretrained(
            "stabilityai/stable-diffusion-xl-base-1.0",
            torch_dtype=torch.float16,
            variant="fp16",
            use_safetensors=True,
        )
        # VRAM 최적화 (Ollama와 공존하기 위한 CPU 오프로딩)
        _pipeline.enable_model_cpu_offload()
    return _pipeline


def generate_image(
    prompt: str,
    negative_prompt: str = "low quality, blurry, distorted, ugly, bad anatomy, watermark, text overlap, poorly drawn",
    output_dir: str = "static/images",
    width: int = 1024,
    height: int = 1024,
    num_inference_steps: int = 30,
    guidance_scale: float = 7.5,
) -> str:
    """
    영어 프롬프트를 받아 SDXL로 고품질 이미지를 생성하고 파일 경로를 반환합니다.
    
    Args:
        prompt: 영어 이미지 생성 프롬프트
        negative_prompt: 생성하지 말아야 할 요소들
        output_dir: 이미지 저장 디렉토리
        width: 이미지 너비 (기본 1024)
        height: 이미지 높이 (기본 1024)
        num_inference_steps: 추론 단계 수 (높을수록 품질↑, 속도↓)
        guidance_scale: 프롬프트 충실도 (높을수록 프롬프트에 충실)
    
    Returns:
        생성된 이미지 파일명
    """
    os.makedirs(output_dir, exist_ok=True)

    pipe = get_pipeline()
    generator = torch.Generator(device="cpu").manual_seed(int(time.time()) % 2**32)

    print(f"Generating image via local SDXL ({width}x{height}, {num_inference_steps} steps)...")

    # 프롬프트 품질 부스터 추가
    enhanced_prompt = f"{prompt}, professional marketing poster, high quality, sharp focus, vibrant colors, commercial photography"

    result = pipe(
        prompt=enhanced_prompt,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        num_inference_steps=num_inference_steps,
        guidance_scale=guidance_scale,
        generator=generator,
    )

    image = result.images[0]
    filename = f"poster_{int(time.time())}.png"
    filepath = os.path.join(output_dir, filename)
    image.save(filepath, "PNG")
    print(f"Image saved to {filepath}")

    return filename
