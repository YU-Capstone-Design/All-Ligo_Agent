"""
로컬 생성형 AI 포스터 이미지 생성 서비스. (기존 image_generator.py)

- 기본 모델: black-forest-labs/FLUX.1-schnell (4 step, guidance 0 → 매우 빠름)
- 폴백 모델: stabilityai/stable-diffusion-xl-base-1.0

FLUX는 Hugging Face 게이트 승인이 필요하고 VRAM 요구량도 크기 때문에,
로드에 실패하면 자동으로 SDXL로 내려갑니다.
기본 설정에서는 로컬 캐시에 있는 모델만 사용합니다 (`IMAGE_MODEL_ALLOW_DOWNLOAD`).

torch / diffusers 는 임포트 비용이 큰 무거운 패키지입니다. 서버 기동 속도를 위해
모듈 최상단이 아니라 실제 생성 시점에 지연 임포트합니다.
"""

import gc
import random
import threading
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# 세로 9:16 숏폼 규격에 맞춘 기본 해상도
DEFAULT_WIDTH = 768
DEFAULT_HEIGHT = 1344

# 모델별 기본 추론 파라미터 (steps, guidance_scale)
_FLUX_DEFAULTS = (4, 0.0)     # schnell은 distilled 모델이라 적은 스텝/무 guidance가 정석
_SDXL_DEFAULTS = (30, 7.5)

FLUX_MODEL_ID = "black-forest-labs/FLUX.1-schnell"
SDXL_MODEL_ID = "stabilityai/stable-diffusion-xl-base-1.0"

# 결과물 품질을 끌어올리기 위해 모든 프롬프트 뒤에 붙이는 공통 태그
_QUALITY_BOOSTER = (
    "professional marketing poster, high quality, sharp focus, "
    "vibrant colors, commercial photography, vertical composition"
)

DEFAULT_NEGATIVE_PROMPT = (
    "low quality, blurry, distorted, ugly, bad anatomy, watermark, text overlap, poorly drawn"
)

# 이미지 모델은 한 번에 하나의 작업만 GPU에 올립니다.
# 동시에 들어온 작업들이 각자 모델을 올리면 VRAM 부족(OOM)으로 함께 실패할 수 있습니다.
# 나중에 온 작업은 앞 작업의 이미지 생성이 끝날 때까지 기다립니다.
_GPU_LOCK = threading.Lock()


def _load_pipeline() -> Tuple[object, str]:
    """
    이미지 생성 파이프라인을 로드하고 (파이프라인, 종류) 튜플을 반환합니다.

    FLUX.1-schnell 로드를 먼저 시도하고, 실패하면 SDXL로 폴백합니다.
    둘 다 실패하면 SDXL 쪽 예외를 그대로 올립니다.
    """
    import torch
    from diffusers import FluxPipeline, StableDiffusionXLPipeline

    # 캐시에 없는 모델을 요청 처리 중에 내려받지 않도록 합니다 (config.py 참고).
    local_only = not settings.IMAGE_MODEL_ALLOW_DOWNLOAD

    logger.info("FLUX.1-schnell 모델 로드를 시도합니다...")
    try:
        pipeline = FluxPipeline.from_pretrained(
            FLUX_MODEL_ID,
            torch_dtype=torch.bfloat16,
            local_files_only=local_only,
        )

        # VRAM 최적화: 다른 모델(Ollama 등)과 GPU를 공유해야 하므로 오프로딩을 켭니다.
        logger.info("FLUX.1-schnell VRAM 최적화 적용 중...")
        pipeline.enable_model_cpu_offload()  # 사용하지 않는 레이어를 CPU로 내림
        pipeline.vae.enable_slicing()        # VAE 디코딩 메모리 절약
        pipeline.vae.enable_tiling()         # 고해상도 생성 시 타일 단위 처리

        logger.info("FLUX.1-schnell 로드 성공.")
        return pipeline, "flux"

    except Exception as flux_err:
        logger.warning(
            "FLUX.1-schnell 로드 실패(로컬 캐시 없음, HF 게이트 인증 또는 VRAM 문제일 수 있음): %s",
            flux_err,
        )
        logger.info("%s 으로 폴백합니다...", SDXL_MODEL_ID)

        try:
            pipeline = StableDiffusionXLPipeline.from_pretrained(
                SDXL_MODEL_ID,
                torch_dtype=torch.float16,
                variant="fp16",
                use_safetensors=True,
                local_files_only=local_only,
            )
            # SDXL은 상대적으로 가벼워 OOM 우려가 낮으므로, CPU 오프로딩 없이
            # 곧바로 CUDA에 올려 생성 속도를 극대화합니다.
            pipeline.to("cuda")

            logger.info("SDXL 폴백 파이프라인 로드 성공 (CUDA 직접 매핑).")
            return pipeline, "sdxl"

        except Exception as sdxl_err:
            logger.error("SDXL 폴백 로드도 실패: %s", sdxl_err)
            raise


def _render_one(
    pipe,
    model_type: str,
    prompt: str,
    negative_prompt: str,
    target_dir: Path,
    width: int,
    height: int,
    num_inference_steps: Optional[int],
    guidance_scale: Optional[float],
) -> str:
    """이미 로드된 파이프라인으로 이미지 1장을 생성·저장하고 파일명을 반환합니다."""
    import torch

    # 매 호출마다 다른 결과를 얻기 위해 무작위 시드를 사용합니다.
    generator = torch.Generator(device="cpu").manual_seed(random.randrange(2**32))
    enhanced_prompt = f"{prompt}, {_QUALITY_BOOSTER}"

    if model_type == "flux":
        default_steps, default_guidance = _FLUX_DEFAULTS
        steps = num_inference_steps if num_inference_steps is not None else default_steps
        guidance = guidance_scale if guidance_scale is not None else default_guidance

        logger.info(
            "FLUX.1-schnell로 이미지 생성 중 (%dx%d, %d steps, guidance=%s)...",
            width, height, steps, guidance,
        )
        # FLUX는 negative_prompt를 지원하지 않습니다.
        result = pipe(
            prompt=enhanced_prompt,
            width=width,
            height=height,
            num_inference_steps=steps,
            guidance_scale=guidance,
            generator=generator,
        )
    else:
        default_steps, default_guidance = _SDXL_DEFAULTS
        steps = num_inference_steps if num_inference_steps is not None else default_steps
        guidance = guidance_scale if guidance_scale is not None else default_guidance

        logger.info(
            "SDXL 폴백으로 이미지 생성 중 (%dx%d, %d steps, guidance=%s)...",
            width, height, steps, guidance,
        )
        result = pipe(
            prompt=enhanced_prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            num_inference_steps=steps,
            guidance_scale=guidance,
            generator=generator,
        )

    # 초 단위 시각만 쓰면 동시에 돌던 작업끼리 같은 파일명을 덮어쓸 수 있어 uuid를 씁니다.
    filename = f"poster_{uuid.uuid4().hex}.png"
    filepath = target_dir / filename
    result.images[0].save(filepath, "PNG")

    logger.info("이미지 저장 완료: %s", filepath)
    return filename


def generate_images(
    prompts: List[str],
    negative_prompt: str = DEFAULT_NEGATIVE_PROMPT,
    output_dir: Optional[Path] = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    num_inference_steps: Optional[int] = None,
    guidance_scale: Optional[float] = None,
) -> List[str]:
    """
    영어 프롬프트 목록으로 세로(9:16) 포스터 이미지들을 생성하고 **파일명 목록**을 반환합니다.

    모델은 한 번만 로드해서 모든 프롬프트에 재사용합니다. (예전에는 장마다 로드/해제를
    반복해서 3장 생성 시 로드 비용을 세 번 냈습니다.)
    한 장이 실패해도 나머지는 계속 생성하며, 성공한 것만 반환합니다.
    모델 로드 자체가 실패하면 예외를 그대로 올립니다.

    이 함수는 **동기(blocking)** 이며 GPU를 오래 점유합니다. 라우터나 코루틴에서
    호출할 때는 반드시 `run_in_threadpool()` 로 감싸세요.

    생성 후에는 파이프라인을 해제하고 CUDA 캐시를 비워, 대기 상태에서 서버의
    VRAM 점유가 0MiB가 되도록 보장합니다.
    """
    import torch

    target_dir = Path(output_dir) if output_dir else settings.IMAGES_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    if _GPU_LOCK.locked():
        logger.info("다른 작업이 이미지 모델을 사용 중입니다. 끝날 때까지 대기합니다...")

    with _GPU_LOCK:
        pipe = None
        try:
            pipe, model_type = _load_pipeline()

            filenames: List[str] = []
            for i, prompt in enumerate(prompts, start=1):
                try:
                    filenames.append(
                        _render_one(
                            pipe, model_type, prompt, negative_prompt, target_dir,
                            width, height, num_inference_steps, guidance_scale,
                        )
                    )
                except Exception as exc:
                    logger.error("이미지 %d/%d 생성 실패(나머지는 계속 진행): %s", i, len(prompts), exc)
            return filenames

        except Exception as exc:
            logger.error("이미지 생성 중 오류: %s", exc)
            raise
        finally:
            # GPU VRAM을 강제로 완전히 해제합니다.
            if pipe is not None:
                logger.info("이미지 파이프라인 해제 및 CUDA 캐시 정리...")
                del pipe
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()


def generate_image(
    prompt: str,
    negative_prompt: str = DEFAULT_NEGATIVE_PROMPT,
    output_dir: Optional[Path] = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    num_inference_steps: Optional[int] = None,
    guidance_scale: Optional[float] = None,
) -> str:
    """
    이미지 1장을 생성하고 **파일명**을 반환합니다. (`generate_images()` 의 단일 버전)

    생성에 실패하면 예외를 던집니다.
    """
    filenames = generate_images(
        [prompt], negative_prompt, output_dir, width, height, num_inference_steps, guidance_scale
    )
    if not filenames:
        raise RuntimeError("이미지 생성에 실패했습니다.")
    return filenames[0]
