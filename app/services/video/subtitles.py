"""
자막 오버레이 생성 모듈.

FFmpeg의 drawtext 필터는 한글 자동 줄바꿈과 글자 단위 애니메이션을 다루기 어렵습니다.
그래서 PIL로 프레임을 한 장씩 직접 그려 PNG 시퀀스를 만들고,
그 시퀀스를 영상 위에 오버레이하는 방식을 씁니다.
"""

import textwrap
from pathlib import Path
from typing import List

from PIL import Image, ImageDraw, ImageFont

from app.core.logging_config import get_logger

from .constants import FONT_BOLD, FONT_FALLBACK, FPS, SHORTS_H, SHORTS_W

logger = get_logger(__name__)

# 자막 줄 수에 따른 (폰트 크기, 줄 높이).
# 줄이 많아질수록 글자를 줄여 화면 밖으로 넘치지 않게 합니다.
_FONT_SCALE_RULES = [
    # (최대 줄 수, 폰트 크기, 줄 높이)
    (4, 58, 78),
    (6, 48, 68),
]
_FONT_SCALE_DEFAULT = (42, 60)  # 7줄 이상일 때

# 하단 그라데이션(자막 가독성용 어둡게 깔기) 높이와 최대 불투명도
_GRADIENT_HEIGHT = 500
_GRADIENT_MAX_ALPHA = 180

# 글자 뒤에 까는 반투명 박스
_TEXT_BOX_PADDING = 15
_TEXT_BOX_RADIUS = 15
_TEXT_BOX_ALPHA = 160

# 타이핑 속도 (초당 글자 수)
DEFAULT_CHARS_PER_SECOND = 20.0


def get_font(size: int) -> ImageFont.FreeTypeFont:
    """자막용 폰트를 로드합니다. 번들 폰트가 없으면 시스템 폴백 폰트를 사용합니다."""
    path = FONT_BOLD if FONT_BOLD.exists() else FONT_FALLBACK
    return ImageFont.truetype(str(path), size)


def _wrap_text(text: str) -> List[str]:
    """
    자막 텍스트를 화면 폭에 맞게 줄바꿈합니다.

    짧은 문구는 좁게(14자) 감싸 큼직하게 보이도록 하고,
    긴 문구는 넓게(18자) 감싸 줄 수를 줄입니다.
    """
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    wrap_width = 14 if len(text) <= 40 else 18

    wrapped: List[str] = []
    for line in lines:
        wrapped.extend(textwrap.wrap(line, width=wrap_width))
    return wrapped


def _pick_font_metrics(line_count: int) -> tuple[int, int]:
    """줄 수에 맞는 (폰트 크기, 줄 높이)를 고릅니다."""
    for max_lines, font_size, line_height in _FONT_SCALE_RULES:
        if line_count <= max_lines:
            return font_size, line_height
    return _FONT_SCALE_DEFAULT


def _draw_bottom_gradient(draw: ImageDraw.ImageDraw) -> None:
    """화면 하단에 위로 갈수록 투명해지는 검은 그라데이션을 그립니다."""
    for y in range(_GRADIENT_HEIGHT):
        alpha = int(_GRADIENT_MAX_ALPHA * (y / _GRADIENT_HEIGHT))
        y_pos = SHORTS_H - _GRADIENT_HEIGHT + y
        draw.rectangle([(0, y_pos), (SHORTS_W, y_pos + 1)], fill=(0, 0, 0, alpha))


def create_typing_overlay_sequence(
    text: str,
    temp_dir: Path,
    total_frames: int,
    fps: int = FPS,
    chars_per_second: float = DEFAULT_CHARS_PER_SECOND,
) -> str:
    """
    한 글자씩 타이핑되는 자막 PNG 시퀀스를 생성하고, FFmpeg용 파일 패턴을 반환합니다.

    Returns:
        "<temp_dir>/typing_frames/text_frame_%04d.png" 형태의 경로 패턴.
    """
    frames_dir = Path(temp_dir) / "typing_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    wrapped_lines = _wrap_text(text)
    full_text = "\n".join(wrapped_lines)
    total_chars = len(full_text)

    font_size, line_height = _pick_font_metrics(len(wrapped_lines))
    font = get_font(font_size)

    # 자막 블록의 시작 y좌표. 줄이 많아 아래에서부터 쌓다가 화면을 넘길 것 같으면
    # 위로 올리되, 최소 상단 여백 100px은 확보합니다.
    total_height = len(wrapped_lines) * line_height
    base_y = max(100, SHORTS_H - 180 - total_height)

    logger.info("자막 타이핑 애니메이션 생성 중: %d 프레임 (%.1f자/초)", total_frames, chars_per_second)

    for frame_idx in range(total_frames):
        # 이 프레임 시점에 몇 글자까지 보여줄지 계산합니다.
        elapsed = frame_idx / fps
        num_chars = min(int(elapsed * chars_per_second), total_chars)

        frame_text = full_text[:num_chars]

        # 투명 캔버스 위에 자막만 그립니다(영상 위에 알파 합성됨).
        overlay = Image.new("RGBA", (SHORTS_W, SHORTS_H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        _draw_bottom_gradient(draw)

        for i, line in enumerate(frame_text.split("\n")):
            if not line:
                continue

            y = base_y + i * line_height
            bbox = draw.textbbox((0, 0), line, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            x = (SHORTS_W - text_w) // 2

            # 배경 박스 → 그림자 → 본문 순서로 겹쳐 그립니다.
            draw.rounded_rectangle(
                [
                    x - _TEXT_BOX_PADDING,
                    y - _TEXT_BOX_PADDING,
                    x + text_w + _TEXT_BOX_PADDING,
                    y + text_h + _TEXT_BOX_PADDING,
                ],
                radius=_TEXT_BOX_RADIUS,
                fill=(0, 0, 0, _TEXT_BOX_ALPHA),
            )
            draw.text((x + 2, y + 2), line, fill=(0, 0, 0, 200), font=font)   # 그림자
            draw.text((x, y), line, fill=(255, 255, 255, 255), font=font)     # 본문

        overlay.save(frames_dir / f"text_frame_{frame_idx:04d}.png", "PNG")

    return str(frames_dir / "text_frame_%04d.png")
