import os
import subprocess
import requests
import time

# FFmpeg 기반 영상 생성 모듈
# 로컬 SDXL 이미지를 세로 숏폼 영상(1080x1920)으로 변환합니다.
# GPU 없이 CPU만으로 수 초 만에 영상을 생성할 수 있습니다.

# 한국어 폰트 경로 (Noto Sans CJK)
FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FALLBACK_FONT_PATH = "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"


def _get_font_path() -> str:
    """시스템에서 사용 가능한 한국어 폰트 경로를 반환합니다."""
    for path in [FONT_PATH, FALLBACK_FONT_PATH]:
        if os.path.exists(path):
            return path
    # fc-list로 한국어 폰트 검색
    try:
        result = subprocess.run(
            ["fc-list", ":lang=ko", "file"], 
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            first_font = result.stdout.strip().split("\n")[0]
            return first_font.split(":")[0].strip()
    except Exception:
        pass
    return "sans"  # 최후의 폴백


def _build_ffmpeg_filter(marketing_text: str, duration: int, fps: int) -> str:
    """FFmpeg 필터 체인을 구성합니다."""
    total_frames = duration * fps

    # Ken Burns 효과: 천천히 1.0x → 1.3x 줌 + 약간의 우상단 패닝
    zoom_expr = f"min(1+0.3*on/{total_frames},1.3)"
    x_expr = f"iw/2-(iw/zoom/2)+on/{total_frames}*50"
    y_expr = f"ih/2-(ih/zoom/2)-on/{total_frames}*30"

    filter_parts = [
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={total_frames}:s=1920x1920:fps={fps}",
        "scale=1080:1080",
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black",
        f"fade=t=in:st=0:d=1,fade=t=out:st={duration-1}:d=1",
    ]

    # 텍스트 오버레이 추가 (텍스트가 있을 때만)
    overlay_text = ""
    if marketing_text:
        lines = [l.strip() for l in marketing_text.split("\n") if l.strip() and not l.strip().startswith("[IMAGE_PROMPT]")]
        overlay_lines = lines[:2] if len(lines) >= 2 else lines
        overlay_text = "\\n".join(overlay_lines)
        overlay_text = overlay_text.replace("'", "\\'").replace('"', '\\"').replace("%", "%%").replace(":", "\\:")

    if overlay_text:
        font_path = _get_font_path()
        filter_parts.append(
            f"drawtext=fontfile='{font_path}':text='{overlay_text}'"
            f":fontsize=36:fontcolor=white:borderw=3:bordercolor=black"
            f":x=(w-text_w)/2:y=h-text_h-120"
        )

    return ",".join(filter_parts)


def _run_ffmpeg(img_path: str, output_dir: str, duration: int, filter_chain: str) -> str:
    """FFmpeg를 실행하여 영상을 생성합니다."""
    filename = f"video_{int(time.time())}.mp4"
    filepath = os.path.join(output_dir, filename)

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", img_path,
        "-t", str(duration),
        "-vf", filter_chain,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        filepath
    ]

    print(f"Generating {duration}s video via FFmpeg (1080x1920 Shorts/Reels)...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        print(f"FFmpeg error: {result.stderr}")
        raise RuntimeError(f"FFmpeg failed: {result.stderr[:500]}")

    print(f"Video saved to {filepath}")
    return filename


def generate_video_from_local(
    image_path: str,
    marketing_text: str = "",
    output_dir: str = "static/videos",
    duration: int = 10,
) -> str:
    """
    로컬 이미지 파일 경로를 받아 FFmpeg로 세로 숏폼 영상(1080x1920)을 생성합니다.
    (SDXL 로컬 이미지 전용)
    """
    os.makedirs(output_dir, exist_ok=True)
    fps = 30
    filter_chain = _build_ffmpeg_filter(marketing_text, duration, fps)
    return _run_ffmpeg(image_path, output_dir, duration, filter_chain)


def generate_video_from_url(
    image_url: str,
    marketing_text: str = "",
    output_dir: str = "static/videos",
    duration: int = 10,
) -> str:
    """
    이미지 URL을 받아 다운로드 후 FFmpeg로 세로 숏폼 영상(1080x1920)을 생성합니다.
    (외부 이미지 URL용, 레거시 호환)
    """
    os.makedirs(output_dir, exist_ok=True)

    print("Downloading image for video generation...")
    img_path = os.path.join(output_dir, f"temp_{int(time.time())}.png")
    response = requests.get(image_url)
    with open(img_path, "wb") as f:
        f.write(response.content)

    fps = 30
    filter_chain = _build_ffmpeg_filter(marketing_text, duration, fps)
    result = _run_ffmpeg(img_path, output_dir, duration, filter_chain)

    # 임시 이미지 파일 정리
    if os.path.exists(img_path):
        os.remove(img_path)

    return result
