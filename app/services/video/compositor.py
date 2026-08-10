"""
영상 합성 단계별 FFmpeg 파이프라인.

전체 흐름:
    원본 이미지 → (preprocess) 9:16 크롭
               → (create_segment) Ken Burns 효과 세그먼트
               → (join_segments) xfade 트랜지션으로 연결
               → (composite_final) 색보정 + 자막 + 오디오 합성
"""

import shutil
from pathlib import Path
from typing import List, Optional

from PIL import Image

from app.core.logging_config import get_logger

from .constants import FPS, KB_PRESETS, NVENC_OPTS, SHORTS_H, SHORTS_W, TRANSITIONS
from .ffmpeg_runner import run_ffmpeg

logger = get_logger(__name__)

# 최종 합성 시 적용할 시네마틱 필터 체인
#   eq      : 밝기/대비/채도 보정
#   unsharp : 선명도 향상
#   vignette: 가장자리를 어둡게 해 중앙 피사체를 강조
_CINEMATIC_FILTER = (
    "eq=brightness=0.02:contrast=1.1:saturation=1.2,"
    "unsharp=5:5:1.0:5:5:0.0,"
    "vignette=PI/4"
)

# 페이드 인/아웃 길이(초)
_FADE_IN_DUR = 0.5
_FADE_OUT_DUR = 0.8
# 오디오 페이드 아웃은 영상 끝나기 1.5초 전부터
_AUDIO_FADE_DUR = 1.5


def preprocess_image(img_path: str, out_path: Path) -> None:
    """
    이미지를 1080x1920(9:16)으로 맞춥니다.

    비율을 유지한 채 화면을 꽉 채우고(cover) 중앙을 잘라내므로,
    가로 사진을 넣어도 찌그러지지 않습니다.
    """
    img = Image.open(img_path).convert("RGB")

    target_ratio = SHORTS_W / SHORTS_H
    img_ratio = img.width / img.height

    # 원본이 목표보다 가로로 넓으면 좌우를, 세로로 길면 상하를 잘라냅니다.
    if img_ratio > target_ratio:
        new_h = img.height
        new_w = int(new_h * target_ratio)
    else:
        new_w = img.width
        new_h = int(new_w / target_ratio)

    left = (img.width - new_w) // 2
    top = (img.height - new_h) // 2

    img = img.crop((left, top, left + new_w, top + new_h))
    img = img.resize((SHORTS_W, SHORTS_H), Image.LANCZOS)
    img.save(out_path, "PNG")


def create_segment(img_path: Path, out_path: Path, idx: int, duration: float, fps: int = FPS) -> None:
    """
    정지 이미지 한 장에 Ken Burns(줌/패닝) 효과를 입힌 영상 세그먼트를 만듭니다.

    프리셋은 인덱스로 순환 선택되어, 연속된 이미지가 서로 다른 움직임을 갖습니다.
    """
    frames = int(duration * fps)
    zoom_expr, x_expr, y_expr = [
        expr.replace("{F}", str(frames)) for expr in KB_PRESETS[idx % len(KB_PRESETS)]
    ]

    video_filter = (
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={frames}"
        f":s={SHORTS_W}x{SHORTS_H}:fps={fps}"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(img_path),
        "-t", str(duration),
        "-vf", video_filter,
        *NVENC_OPTS,
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    run_ffmpeg(cmd, timeout=60)


def join_segments(
    segments: List[Path],
    out_path: Path,
    transition_dur: float,
    segment_durations: List[float],
) -> None:
    """
    여러 세그먼트를 xfade 트랜지션으로 이어붙입니다.

    xfade는 두 입력을 겹쳐 전환하므로, i번째 전환의 offset은
    "앞선 세그먼트 길이의 합 - 지금까지 겹친 시간(i * transition_dur)"이 됩니다.
    """
    if len(segments) == 1:
        # 세그먼트가 하나면 트랜지션이 필요 없어 그대로 복사합니다.
        shutil.copy(segments[0], out_path)
        return

    inputs: List[str] = []
    for segment in segments:
        inputs.extend(["-i", str(segment)])

    # xfade 체인을 순차적으로 구성합니다: [0]+[1]→[v1], [v1]+[2]→[v2], ... →[vout]
    filter_parts: List[str] = []
    prev_label = "[0]"

    for i in range(1, len(segments)):
        transition = TRANSITIONS[i % len(TRANSITIONS)]
        offset = round(sum(segment_durations[:i]) - i * transition_dur, 2)
        out_label = f"[v{i}]" if i < len(segments) - 1 else "[vout]"

        filter_parts.append(
            f"{prev_label}[{i}]xfade=transition={transition}"
            f":duration={transition_dur}:offset={offset}{out_label}"
        )
        prev_label = out_label

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vout]",
        *NVENC_OPTS,
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    run_ffmpeg(cmd, timeout=120)


def _build_audio_filter(
    inputs: List[str],
    tts_path: Optional[Path],
    bgm_path: Optional[str],
    first_audio_idx: int,
    total_dur: float,
) -> str:
    """
    오디오 믹싱 필터를 구성하고, `inputs` 에 오디오 입력을 덧붙입니다.

    TTS와 BGM이 모두 있으면 나레이션을 크게(1.2), BGM을 작게(0.25) 섞습니다.
    오디오가 하나도 없으면 빈 문자열을 반환합니다(= 무음 영상).
    """
    fade_start = max(0.0, total_dur - _AUDIO_FADE_DUR)

    if tts_path and bgm_path:
        inputs.extend(["-i", str(tts_path), "-i", str(bgm_path)])
        tts_idx, bgm_idx = first_audio_idx, first_audio_idx + 1
        return (
            f"[{tts_idx}:a]volume=1.2[a1];"
            f"[{bgm_idx}:a]volume=0.25[a2];"
            f"[a1][a2]amix=inputs=2:duration=longest,"
            f"afade=t=out:st={fade_start}:d={_AUDIO_FADE_DUR}[aout]"
        )

    if tts_path:
        inputs.extend(["-i", str(tts_path)])
        return f"[{first_audio_idx}:a]volume=1.2,afade=t=out:st={fade_start}:d={_AUDIO_FADE_DUR}[aout]"

    if bgm_path:
        inputs.extend(["-i", str(bgm_path)])
        return f"[{first_audio_idx}:a]volume=0.3,afade=t=out:st={fade_start}:d={_AUDIO_FADE_DUR}[aout]"

    return ""


def composite_final(
    video_path: Path,
    overlay_pattern: Optional[str],
    out_path: Path,
    total_dur: float,
    tts_path: Optional[Path] = None,
    bgm_path: Optional[str] = None,
) -> None:
    """
    색보정 + 자막 오버레이 + 오디오를 합쳐 최종 MP4를 만듭니다.

    Args:
        video_path: 트랜지션까지 끝난 중간 영상.
        overlay_pattern: 자막 PNG 시퀀스 패턴. None이면 자막 없이 합성합니다.
        total_dur: 최종 영상 길이(초).
        tts_path: 나레이션 mp3 경로(선택).
        bgm_path: 배경음악 mp3 경로(선택).
    """
    fade_out_start = total_dur - _FADE_OUT_DUR

    if overlay_pattern:
        # 자막 시퀀스를 두 번째 입력으로 받아 알파 합성합니다.
        # -pix_fmt를 지정하지 않고 overlay=format=auto를 써야 RGBA 알파가 보존됩니다.
        inputs = ["-i", str(video_path), "-framerate", str(FPS), "-i", overlay_pattern]
        video_filter = (
            f"[0:v]{_CINEMATIC_FILTER}[v_f];"
            f"[1:v]format=yuva420p[ovr];"
            f"[v_f][ovr]overlay=0:0:format=auto,"
            f"fade=t=in:st=0:d={_FADE_IN_DUR},"
            f"fade=t=out:st={fade_out_start}:d={_FADE_OUT_DUR}[vout]"
        )
    else:
        inputs = ["-i", str(video_path)]
        video_filter = (
            f"[0:v]{_CINEMATIC_FILTER},"
            f"fade=t=in:st=0:d={_FADE_IN_DUR},"
            f"fade=t=out:st={fade_out_start}:d={_FADE_OUT_DUR}[vout]"
        )

    # 오디오 입력의 시작 인덱스는 영상 입력 개수에 따라 달라집니다.
    first_audio_idx = 2 if overlay_pattern else 1
    audio_filter = _build_audio_filter(inputs, tts_path, bgm_path, first_audio_idx, total_dur)

    filter_complex = video_filter
    if audio_filter:
        filter_complex += ";" + audio_filter

    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", filter_complex, "-map", "[vout]"]

    if audio_filter:
        cmd.extend(["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"])

    cmd.extend([
        *NVENC_OPTS,
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",  # 웹 스트리밍 시 즉시 재생 가능하도록 메타데이터를 앞으로
        "-t", str(total_dur),
        str(out_path),
    ])

    run_ffmpeg(cmd, timeout=120)
