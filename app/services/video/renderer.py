"""
숏폼 영상 렌더링 오케스트레이터.

이미지 목록과 마케팅 텍스트를 받아 최종 MP4 한 편을 만들어내는 전체 흐름을
단계별로 조립합니다. 실제 작업은 각 단계 모듈(compositor / audio / subtitles)에
위임하고, 여기서는 "순서와 시간 계산"만 담당합니다.
"""

import shutil
import subprocess
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from app.core.config import settings
from app.core.logging_config import get_logger

from . import audio, compositor
from .constants import FPS
from .ffmpeg_runner import probe_duration
from .subtitles import create_typing_overlay_sequence

logger = get_logger(__name__)

# TTS 나레이션이 끝난 뒤 남겨두는 여운 시간(초)
_OUTRO_PADDING_SEC = 1.5
# 마지막 세그먼트가 지나치게 짧아지지 않도록 보장하는 최소 길이(초)
_MIN_LAST_SEGMENT_SEC = 1.0


def _generate_narration(marketing_text: str, temp_dir: Path) -> Tuple[Optional[Path], float]:
    """
    마케팅 텍스트로 TTS 나레이션을 만들고 (경로, 길이) 를 반환합니다.

    TTS는 영상 전체 길이를 결정하는 기준이므로 가장 먼저 처리합니다.
    실패하면 (None, 0.0) 을 반환하고 영상은 무음/BGM만으로 진행됩니다.
    """
    if not marketing_text.strip():
        return None, 0.0

    tts_path = temp_dir / "tts_voice.mp3"
    try:
        audio.create_tts_audio(marketing_text, tts_path)
        if tts_path.exists():
            duration = probe_duration(str(tts_path))
            logger.info("TTS 길이 감지: %.2fs", duration)
            return tts_path, duration
    except subprocess.CalledProcessError as exc:
        # edge-tts 는 Microsoft 온라인 서비스를 쓰므로 네트워크 문제로도 실패합니다.
        logger.warning(
            "TTS 생성 실패(%s). 나레이션 없이 진행합니다. stderr: %s",
            exc, (exc.stderr or "")[-500:],
        )
    except Exception as exc:
        logger.warning("TTS 생성/길이 측정 실패(%s). 나레이션 없이 진행합니다.", exc)

    return None, 0.0


def _plan_segment_durations(
    num_images: int,
    target_total_dur: float,
    beat_transitions: List[float],
    transition_dur: float,
) -> Tuple[List[float], float, float]:
    """
    비트 전환 지점을 바탕으로 각 세그먼트의 재생 시간을 계산합니다.

    세그먼트는 트랜지션 구간만큼 서로 겹치므로, 각 세그먼트 길이에
    transition_dur을 더해두고 총 길이에서는 겹친 만큼을 빼줍니다.

    Returns:
        (세그먼트별 길이 목록, 최종 총 길이, 실제 적용할 트랜지션 길이)
    """
    # 이미지가 한 장이면 전환이 없으므로 트랜지션 길이를 0으로 만듭니다.
    if num_images == 1:
        return [target_total_dur], target_total_dur, 0.0

    durations: List[float] = []

    # 첫 세그먼트: 시작부터 첫 전환 지점까지
    durations.append(beat_transitions[0] + transition_dur)

    # 중간 세그먼트들: 인접한 전환 지점 사이의 간격
    for i in range(1, num_images - 1):
        durations.append(beat_transitions[i] - beat_transitions[i - 1] + transition_dur)

    # 마지막 세그먼트: 남은 시간 전부 (단, 최소 길이 보장)
    last_dur = target_total_dur - beat_transitions[-1] + transition_dur
    durations.append(max(last_dur, _MIN_LAST_SEGMENT_SEC))

    # 겹치는 구간을 제외한 실제 총 재생 시간
    total_dur = round(sum(durations) - (num_images - 1) * transition_dur, 2)
    return durations, total_dur, transition_dur


def create_shortform_video(
    image_paths: List[str],
    marketing_text: str,
    output_dir: Optional[Path] = None,
    seconds_per_image: float = 3.0,
    transition_duration: float = 0.7,
) -> str:
    """
    1~5장의 이미지와 마케팅 텍스트로 숏폼 영상을 생성하고 **파일명**을 반환합니다.

    적용되는 연출:
      - BGM 비트 분석(librosa)에 맞춘 다이내믹 컷 편집
      - 이미지마다 다른 Ken Burns 효과 (줌인/줌아웃/패닝)
      - hblur, zoomin 등 xfade 트랜지션
      - PIL로 그린 타이핑 자막 애니메이션
      - TTS 나레이션 + BGM 믹싱, 색보정, 페이드 인/아웃
      - 최종 출력: 1080x1920 (9:16) MP4

    이 함수는 **동기(blocking)** 이며 수십 초가 걸립니다.
    라우터/코루틴에서는 `run_in_threadpool()` 로 감싸 호출하세요.
    """
    target_dir = Path(output_dir) if output_dir else settings.VIDEOS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    # 중간 산출물(전처리 이미지, 세그먼트, 자막 프레임)을 담을 임시 폴더.
    # 작업마다 고유해야 합니다. 같은 이름을 쓰면 먼저 끝난 작업이 다른 작업의 폴더를 지워버립니다.
    job_id = uuid.uuid4().hex
    temp_dir = target_dir / f"_tmp_{job_id}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    num_images = len(image_paths)

    try:
        # --- 1. 이미지 전처리 (9:16 크롭) ---
        processed_images: List[Path] = []
        for i, path in enumerate(image_paths):
            processed_path = temp_dir / f"img_{i}.png"
            compositor.preprocess_image(path, processed_path)
            processed_images.append(processed_path)

        # --- 2. TTS 나레이션 생성 (영상 길이의 기준) ---
        tts_path, tts_duration = _generate_narration(marketing_text, temp_dir)

        # 나레이션이 있으면 그 길이 + 여운, 없으면 이미지 수 × 기본 노출 시간
        if tts_duration > 0.0:
            target_total_dur = round(tts_duration + _OUTRO_PADDING_SEC, 2)
        else:
            target_total_dur = round(num_images * seconds_per_image, 2)
        logger.info("목표 영상 길이: %.2fs", target_total_dur)

        # --- 3. BGM 선택 및 비트 분석 ---
        bgm_path = audio.get_random_bgm()
        avg_dur = target_total_dur / num_images
        beat_transitions = audio.analyze_bgm_beats(
            bgm_path,
            num_segments=num_images,
            # 컷이 너무 잦아지지 않도록 평균 노출 시간의 60%를 최소 간격으로 둡니다.
            min_interval=max(1.5, avg_dur * 0.6),
            fallback_interval=avg_dur,
        )

        # --- 4. 세그먼트 길이 계산 ---
        segment_durations, total_dur, transition_dur = _plan_segment_durations(
            num_images, target_total_dur, beat_transitions, transition_duration
        )
        logger.info("세그먼트별 길이: %s", segment_durations)
        logger.info("최종 영상 길이: %.2fs", total_dur)

        # --- 5. Ken Burns 세그먼트 생성 ---
        segments: List[Path] = []
        for i, processed_path in enumerate(processed_images):
            segment_path = temp_dir / f"seg_{i}.mp4"
            compositor.create_segment(processed_path, segment_path, i, segment_durations[i])
            segments.append(segment_path)
            logger.info("세그먼트 %d/%d 생성 완료 (%.2fs)", i + 1, num_images, segment_durations[i])

        # --- 6. 트랜지션으로 이어붙이기 ---
        joined_path = temp_dir / "joined.mp4"
        compositor.join_segments(segments, joined_path, transition_dur, segment_durations)
        logger.info("세그먼트 연결 완료.")

        # --- 7. 자막 시퀀스 생성 ---
        overlay_pattern = None
        if marketing_text.strip():
            overlay_pattern = create_typing_overlay_sequence(
                marketing_text, temp_dir, total_frames=int(total_dur * FPS), fps=FPS
            )
            logger.info("자막 타이핑 시퀀스 생성 완료.")

        # --- 8. 최종 합성 ---
        filename = f"shortform_{job_id}.mp4"
        final_path = target_dir / filename

        compositor.composite_final(
            joined_path,
            overlay_pattern,
            final_path,
            total_dur,
            tts_path=tts_path,
            bgm_path=bgm_path,
        )

        logger.info("숏폼 영상 저장 완료: %s (%.2fs)", final_path, total_dur)
        return filename

    except Exception as exc:
        logger.error("숏폼 영상 생성 실패: %s", exc)
        raise
    finally:
        # 성공/실패와 무관하게 임시 폴더는 항상 정리합니다.
        shutil.rmtree(temp_dir, ignore_errors=True)


def generate_video_from_local(
    image_path: str,
    marketing_text: str = "",
    output_dir: Optional[Path] = None,
    duration: int = 10,
) -> str:
    """
    단일 이미지를 숏폼 영상으로 변환합니다. (레거시 호환용 헬퍼)

    내부적으로는 `create_shortform_video()` 를 이미지 1장으로 호출합니다.
    """
    return create_shortform_video(
        [image_path],
        marketing_text,
        output_dir,
        seconds_per_image=float(duration),
        transition_duration=0.0,
    )
