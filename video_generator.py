import os
import subprocess
import requests
import time
import textwrap

# FFmpeg 기반 영상 생성 모듈
# 로컬 SDXL 세로 이미지(768x1344)를 숏폼 영상(1080x1920)으로 변환합니다.
# GPU 없이 CPU만으로 수 초 만에 영상을 생성할 수 있습니다.

# 한국어 폰트 경로 (Noto Sans CJK)
FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FALLBACK_FONT_PATH = "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"

# 자막 설정
MAX_CHARS_PER_LINE = 16  # 세로 영상(1080px) 기준 한 줄 최대 글자 수
MAX_OVERLAY_LINES = 4    # 최대 표시 줄 수


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


def _wrap_text(text: str) -> str:
    """
    마케팅 텍스트를 세로 영상 너비에 맞게 줄바꿈 처리합니다.
    한국어는 글자 폭이 넓으므로 한 줄당 MAX_CHARS_PER_LINE 글자로 제한합니다.
    """
    if not text:
        return ""
    
    # IMAGE_PROMPT 부분 제거
    lines = [l.strip() for l in text.split("\n") 
             if l.strip() and not l.strip().startswith("[IMAGE_PROMPT]")]
    
    # 각 줄을 MAX_CHARS_PER_LINE 글자로 줄바꿈
    wrapped_lines = []
    for line in lines:
        if len(line) <= MAX_CHARS_PER_LINE:
            wrapped_lines.append(line)
        else:
            # textwrap으로 줄바꿈 (한국어도 정상 동작)
            sub_lines = textwrap.wrap(line, width=MAX_CHARS_PER_LINE)
            wrapped_lines.extend(sub_lines)
    
    # 최대 줄 수 제한
    wrapped_lines = wrapped_lines[:MAX_OVERLAY_LINES]
    
    return wrapped_lines


def _escape_ffmpeg_text(text: str) -> str:
    """FFmpeg drawtext 필터용 특수문자 이스케이프 처리"""
    # FFmpeg drawtext에서 특수하게 취급되는 문자들을 이스케이프
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "'\\''")
    text = text.replace("%", "%%")
    text = text.replace(":", "\\:")
    text = text.replace(";", "\\;")
    return text


def _build_ffmpeg_filter(marketing_text: str, duration: int, fps: int) -> str:
    """FFmpeg 필터 체인을 구성합니다. 세로 영상(1080x1920)에 최적화."""
    total_frames = duration * fps

    # Ken Burns 효과: 천천히 1.0x → 1.15x 줌 (세로 영상이라 줌 정도를 약간 줄임)
    zoom_expr = f"min(1+0.15*on/{total_frames},1.15)"
    x_expr = f"iw/2-(iw/zoom/2)+on/{total_frames}*20"
    y_expr = f"ih/2-(ih/zoom/2)-on/{total_frames}*15"

    # 세로 이미지(768x1344)를 1080x1920으로 업스케일
    filter_parts = [
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={total_frames}:s=1080x1890:fps={fps}",
        "scale=1080:1920:force_original_aspect_ratio=decrease",
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black",
        f"fade=t=in:st=0:d=1,fade=t=out:st={duration-1}:d=1",
    ]

    # 텍스트 오버레이: 줄바꿈 처리된 각 줄을 별도의 drawtext로 추가
    wrapped_lines = _wrap_text(marketing_text)
    if wrapped_lines:
        font_path = _get_font_path()
        font_size = 38
        line_height = 52  # 줄 간격
        # 하단에서부터 위로 쌓이도록 y좌표 계산
        total_text_height = len(wrapped_lines) * line_height
        base_y = 1920 - total_text_height - 150  # 하단 여백 150px

        for i, line in enumerate(wrapped_lines):
            escaped_line = _escape_ffmpeg_text(line)
            y_pos = base_y + (i * line_height)
            filter_parts.append(
                f"drawtext=fontfile='{font_path}'"
                f":text='{escaped_line}'"
                f":fontsize={font_size}"
                f":fontcolor=white"
                f":borderw=3:bordercolor=black@0.8"
                f":x=(w-text_w)/2"
                f":y={y_pos}"
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
