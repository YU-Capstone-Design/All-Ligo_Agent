"""
프로페셔널 숏폼 영상 생성기 (FFmpeg + PIL)
Instagram Reels / YouTube Shorts 품질의 광고 영상을 이미지로부터 생성합니다.
"""
import os
import subprocess
import time
import textwrap
import shutil
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from typing import List

# === Constants ===
SHORTS_W, SHORTS_H = 1080, 1920
FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
FONT_BOLD = os.path.join(FONT_DIR, "Pretendard-ExtraBold.otf")
FONT_FALLBACK = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

# Ken Burns 프리셋 (이미지마다 다른 움직임)
KB_PRESETS = [
    # zoom_expr, x_expr, y_expr  ('{F}'는 총 프레임 수로 치환됨)
    ("min(1+0.15*on/{F},1.15)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),                      # 중앙 줌인
    ("min(1+0.12*on/{F},1.12)", "iw/2-(iw/zoom/2)+on/{F}*60", "ih/2-(ih/zoom/2)"),             # 우측 줌인
    ("1.12", "(iw-iw/zoom)*(1-on/{F})", "ih/3-(ih/zoom/3)"),                                   # 좌로 패닝
    ("1.15-0.15*on/{F}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),                              # 줌아웃
    ("min(1+0.12*on/{F},1.12)", "iw/2-(iw/zoom/2)-on/{F}*60", "ih/2-(ih/zoom/2)+on/{F}*20"),   # 좌측 줌인
]

TRANSITIONS = ["fade", "fadeblack", "slideleft", "slideright", "wiperight", "circlecrop"]


def _get_font(size: int) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD if os.path.exists(FONT_BOLD) else FONT_FALLBACK
    return ImageFont.truetype(path, size)


def _preprocess_image(img_path: str, out_path: str):
    """이미지를 1080x1920(9:16)으로 리사이즈+크롭합니다."""
    img = Image.open(img_path).convert("RGB")
    # cover crop: 비율 유지하며 꽉 채운 뒤 중앙 크롭
    target_ratio = SHORTS_W / SHORTS_H
    img_ratio = img.width / img.height
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


def _create_text_overlay(text: str, out_path: str):
    """PIL로 반투명 그라데이션 배경 + 대형 한글 자막 오버레이 PNG를 생성합니다."""
    overlay = Image.new("RGBA", (SHORTS_W, SHORTS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # 하단 그라데이션 배경 (아래로 갈수록 진해짐)
    grad_h = 500
    for y in range(grad_h):
        alpha = int(180 * (y / grad_h))
        draw.rectangle([(0, SHORTS_H - grad_h + y), (SHORTS_W, SHORTS_H - grad_h + y + 1)],
                       fill=(0, 0, 0, alpha))

    # 텍스트 줄바꿈 (한 줄 14글자, 최대 4줄)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    wrapped = []
    for line in lines:
        wrapped.extend(textwrap.wrap(line, width=14))
    wrapped = wrapped[:4]

    if not wrapped:
        overlay.save(out_path, "PNG")
        return

    font_size = 58
    font = _get_font(font_size)
    line_h = 78
    total_h = len(wrapped) * line_h
    base_y = SHORTS_H - 180 - total_h

    for i, line in enumerate(wrapped):
        y = base_y + i * line_h
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        x = (SHORTS_W - tw) // 2
        # 텍스트 그림자 (2px offset)
        draw.text((x + 2, y + 2), line, fill=(0, 0, 0, 200), font=font)
        # 메인 텍스트 (흰색)
        draw.text((x, y), line, fill=(255, 255, 255, 255), font=font)

    overlay.save(out_path, "PNG")


def _create_segment(img_path: str, out_path: str, idx: int, duration: float, fps: int = 30):
    """하나의 이미지에 Ken Burns 효과를 적용한 영상 세그먼트를 생성합니다."""
    frames = int(duration * fps)
    preset = KB_PRESETS[idx % len(KB_PRESETS)]
    z, x, y = [expr.replace("{F}", str(frames)) for expr in preset]

    vf = (f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}"
          f":s={SHORTS_W}x{SHORTS_H}:fps={fps}")

    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", img_path, "-t", str(duration),
           "-vf", vf, "-c:v", "libx264", "-preset", "fast", "-crf", "20",
           "-pix_fmt", "yuv420p", out_path]
    subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True)


def _join_segments(segments: List[str], out_path: str, td: float, spi: float):
    """여러 세그먼트를 xfade 트랜지션으로 이어붙입니다."""
    if len(segments) == 1:
        shutil.copy(segments[0], out_path)
        return

    inputs = []
    for s in segments:
        inputs.extend(["-i", s])

    # xfade 체인 구성
    fc_parts = []
    prev = "[0]"
    for i in range(1, len(segments)):
        trans = TRANSITIONS[i % len(TRANSITIONS)]
        offset = round((i) * spi - i * td, 2)
        out_label = f"[v{i}]" if i < len(segments) - 1 else "[vout]"
        fc_parts.append(f"{prev}[{i}]xfade=transition={trans}:duration={td}:offset={offset}{out_label}")
        prev = out_label

    fc = ";".join(fc_parts)

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", fc,
        "-map", "[vout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", out_path]
    subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True)


def _composite_final(video_path: str, overlay_path: str, out_path: str, total_dur: float):
    """
    영상 위에 시네마틱 필터를 입히고 텍스트 오버레이를 합성합니다.
    - eq/unsharp: 색감 보정 및 선명도 향상
    - vignette: 하단 가독성을 위한 비네팅 효과
    """
    # 필터 체인: 색감 보정(eq) + 선명도(unsharp) + 비네팅(vignette)
    video_filter = (
        "eq=brightness=0.02:contrast=1.1:saturation=1.2,"
        "unsharp=5:5:1.0:5:5:0.0,"
        "vignette=PI/4"
    )
    
    # 합성 필터 체인
    # [0:v]에 영상 필터 적용 -> [v_filtered]
    # [1:v] 오버레이에 페이드 인 적용 -> [ovr]
    # [v_filtered] 위에 [ovr] 합성 -> 최종 페이드 인/아웃
    fc = (
        f"[0:v]{video_filter}[v_f];"
        f"[1:v]format=rgba,fade=t=in:st=0.5:d=0.8:alpha=1[ovr];"
        f"[v_f][ovr]overlay=0:0,"
        f"fade=t=in:st=0:d=0.5,fade=t=out:st={total_dur - 0.8}:d=0.8"
    )

    cmd = ["ffmpeg", "-y", "-i", video_path, "-i", overlay_path,
           "-filter_complex", fc,
           "-c:v", "libx264", "-preset", "fast", "-crf", "20",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-t", str(total_dur),
           out_path]
    subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True)


# ===== Public API =====

def create_shortform_video(
    image_paths: List[str],
    marketing_text: str,
    output_dir: str = "static/videos",
    seconds_per_image: float = 3.0,
    transition_duration: float = 0.7,
) -> str:
    """
    1~5장의 이미지와 마케팅 텍스트로 프로페셔널 숏폼 영상을 생성합니다.
    
    - 각 이미지에 다양한 Ken Burns 효과 (줌인/줌아웃/패닝)
    - 이미지 전환: fade, slide, wipe, circlecrop 등 광고 스타일 트랜지션
    - PIL 렌더링 대형 한글 자막 + 그라데이션 배경
    - 최종 출력: 1080x1920 (9:16) MP4
    """
    os.makedirs(output_dir, exist_ok=True)
    temp = os.path.join(output_dir, f"_tmp_{int(time.time())}")
    os.makedirs(temp, exist_ok=True)

    spi = seconds_per_image
    td = transition_duration
    n = len(image_paths)

    try:
        # 1. 이미지 전처리 (9:16 크롭)
        processed = []
        for i, p in enumerate(image_paths):
            pp = os.path.join(temp, f"img_{i}.png")
            _preprocess_image(p, pp)
            processed.append(pp)

        # 2. Ken Burns 세그먼트 생성
        segments = []
        for i, p in enumerate(processed):
            sp = os.path.join(temp, f"seg_{i}.mp4")
            _create_segment(p, sp, i, spi)
            segments.append(sp)
            print(f"  Segment {i+1}/{n} created.")

        # 3. 트랜지션으로 이어붙이기
        joined = os.path.join(temp, "joined.mp4")
        _join_segments(segments, joined, td, spi)
        print("  Segments joined with transitions.")

        # 4. 텍스트 오버레이 생성
        overlay_png = os.path.join(temp, "text_overlay.png")
        _create_text_overlay(marketing_text, overlay_png)

        # 5. 최종 합성
        total_dur = round(n * spi - max(0, n - 1) * td, 2)
        filename = f"shortform_{int(time.time())}.mp4"
        final_path = os.path.join(output_dir, filename)
        _composite_final(joined, overlay_png, final_path, total_dur)

        print(f"Shortform video saved: {final_path} ({total_dur}s)")
        return filename

    finally:
        shutil.rmtree(temp, ignore_errors=True)


# ===== Legacy (단일 이미지 → 영상) =====

def generate_video_from_local(
    image_path: str, marketing_text: str = "",
    output_dir: str = "static/videos", duration: int = 10,
) -> str:
    """단일 로컬 이미지를 숏폼 영상으로 변환합니다."""
    return create_shortform_video(
        [image_path], marketing_text, output_dir,
        seconds_per_image=float(duration), transition_duration=0.0
    )
