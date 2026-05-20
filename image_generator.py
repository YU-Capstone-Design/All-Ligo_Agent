import os
import torch
from diffusers import FluxPipeline, StableDiffusionXLPipeline
from PIL import Image
from typing import Optional
import time

# 전역 변수로 파이프라인 유지 (한 번만 로드하기 위함)
_pipeline = None
_pipeline_type = None  # "flux" 또는 "sdxl"

# 로컬 생성형 AI 이미지 생성 모듈
# 기본 모델: black-forest-labs/FLUX.1-schnell (rtx 4080 고고화질)
# 폴백 모델: stabilityai/stable-diffusion-xl-base-1.0 (인증 제한 또는 메모리 문제 시 자동 대처)

def get_pipeline():
    """FLUX.1-schnell 파이프라인을 로드합니다. 실패 시 SDXL 모델로 자동 폴백합니다."""
    global _pipeline, _pipeline_type
    if _pipeline is None:
        print("Attempting to load FLUX.1-schnell Model...")
        try:
            # bfloat16 정밀도로 FLUX.1-schnell 로드 시도
            _pipeline = FluxPipeline.from_pretrained(
                "black-forest-labs/FLUX.1-schnell",
                torch_dtype=torch.bfloat16,
            )
            
            # VRAM 최적화 적용
            print("Applying VRAM optimizations for FLUX.1-schnell...")
            _pipeline.enable_model_cpu_offload()  # GPU VRAM 공존을 위한 오프로딩
            _pipeline.vae.enable_slicing()         # VAE 디코딩 메모리 절약
            _pipeline.vae.enable_tiling()          # 고해상도 생성 시 VAE 타일링
            
            _pipeline_type = "flux"
            print("Successfully loaded FLUX.1-schnell.")
            
        except Exception as flux_err:
            print(f"Failed to load FLUX.1-schnell (possibly due to gated Hugging Face auth or VRAM): {flux_err}")
            print("Falling back to stabilityai/stable-diffusion-xl-base-1.0...")
            try:
                # SDXL로 폴백
                _pipeline = StableDiffusionXLPipeline.from_pretrained(
                    "stabilityai/stable-diffusion-xl-base-1.0",
                    torch_dtype=torch.float16,
                    variant="fp16",
                    use_safetensors=True,
                )
                # VRAM 최적화 적용
                _pipeline.enable_model_cpu_offload()
                
                _pipeline_type = "sdxl"
                print("Successfully loaded SDXL fallback pipeline.")
            except Exception as sdxl_err:
                print(f"Failed to load SDXL fallback: {sdxl_err}")
                raise sdxl_err
                
    return _pipeline, _pipeline_type


def generate_image(
    prompt: str,
    negative_prompt: str = "low quality, blurry, distorted, ugly, bad anatomy, watermark, text overlap, poorly drawn",
    output_dir: str = "static/images",
    width: int = 768,
    height: int = 1344,
    num_inference_steps: Optional[int] = None,
    guidance_scale: Optional[float] = None,
) -> str:
    """
    영어 프롬프트를 받아 이미지 생성 모델(Flux 또는 SDXL 폴백)로 세로(9:16) 고품질 이미지를 생성하고 파일명을 반환합니다.
    
    Args:
        prompt: 영어 이미지 생성 프롬프트
        negative_prompt: SDXL 폴백 시 제외할 요소들
        output_dir: 이미지 저장 디렉토리
        width: 이미지 너비 (기본 768)
        height: 이미지 높이 (기본 1344)
        num_inference_steps: 추론 단계 수
        guidance_scale: 프롬프트 충실도
    
    Returns:
        생성된 이미지 파일명
    """
    os.makedirs(output_dir, exist_ok=True)

    try:
        pipe, model_type = get_pipeline()
    except Exception as e:
        print(f"Pipeline retrieval failed: {e}")
        raise e

    # 시드 생성
    generator = torch.Generator(device="cpu").manual_seed(int(time.time()) % 2**32)

    # 프롬프트 품질 부스터 추가 (긍정 태그 구성)
    enhanced_prompt = f"{prompt}, professional marketing poster, high quality, sharp focus, vibrant colors, commercial photography, vertical composition"

    try:
        if model_type == "flux":
            steps = num_inference_steps if num_inference_steps is not None else 4
            guidance = guidance_scale if guidance_scale is not None else 0.0
            print(f"Generating image via FLUX.1-schnell ({width}x{height}, {steps} steps, guidance={guidance})...")
            
            result = pipe(
                prompt=enhanced_prompt,
                width=width,
                height=height,
                num_inference_steps=steps,
                guidance_scale=guidance,
                generator=generator,
            )
        else:  # sdxl 폴백
            steps = num_inference_steps if num_inference_steps is not None else 30
            guidance = guidance_scale if guidance_scale is not None else 7.5
            print(f"Generating image via SDXL fallback ({width}x{height}, {steps} steps, guidance={guidance})...")
            
            result = pipe(
                prompt=enhanced_prompt,
                negative_prompt=negative_prompt,
                width=width,
                height=height,
                num_inference_steps=steps,
                guidance_scale=guidance,
                generator=generator,
            )
            
        image = result.images[0]
        filename = f"poster_{int(time.time())}.png"
        filepath = os.path.join(output_dir, filename)
        image.save(filepath, "PNG")
        print(f"Image successfully saved to {filepath}")
        return filename
        
    except Exception as e:
        print(f"Error during image generation: {e}")
        raise e
