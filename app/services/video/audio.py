"""
영상 오디오 트랙 구성 모듈 — TTS 나레이션, 배경음악(BGM) 선택, 비트 분석.

BGM의 비트 위치를 분석해 화면 전환 타이밍을 음악에 맞추면
훨씬 완성도 높은 숏폼 영상이 됩니다.
"""

import glob
import random
import subprocess
from pathlib import Path
from typing import List, Optional

from app.core.config import settings
from app.core.logging_config import get_logger

from .constants import TTS_VOICE

logger = get_logger(__name__)


def create_tts_audio(text: str, out_path: Path, voice: str = TTS_VOICE) -> None:
    """
    edge-tts로 텍스트를 음성 파일(mp3)로 변환합니다.

    말이 너무 늘어지지 않도록 재생 속도를 +10%로 올립니다.
    빈 문자열이면 아무것도 하지 않습니다.
    """
    clean_text = text.replace("\n", " ").strip()
    if not clean_text:
        return

    logger.info("TTS 나레이션 생성 중 (voice=%s)...", voice)
    cmd = [
        "edge-tts",
        "--voice", voice,
        "--rate=+10%",
        "--text", clean_text,
        "--write-media", str(out_path),
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True)


def get_random_bgm(bgm_dir: Optional[Path] = None) -> Optional[str]:
    """
    static/bgm 폴더에서 mp3 하나를 무작위로 고릅니다.

    폴더가 없거나 mp3가 하나도 없으면 None을 반환합니다(= BGM 없이 진행).
    """
    target_dir = Path(bgm_dir) if bgm_dir else settings.BGM_DIR

    if not target_dir.exists():
        return None

    candidates = glob.glob(str(target_dir / "*.mp3"))
    return random.choice(candidates) if candidates else None


def analyze_bgm_beats(
    bgm_path: Optional[str],
    num_segments: int,
    min_interval: float = 1.8,
    fallback_interval: float = 2.5,
) -> List[float]:
    """
    BGM의 비트 위치를 분석해 화면 전환 타임스탬프 목록을 반환합니다.

    이미지가 N장이면 전환 지점은 N-1개가 필요합니다.

    Args:
        bgm_path: 분석할 mp3 경로. None이면 폴백 간격을 사용합니다.
        num_segments: 이미지(세그먼트) 개수.
        min_interval: 전환 사이의 최소 간격(초). 너무 잦은 컷을 방지합니다.
        fallback_interval: 비트 분석 실패 시 사용할 균등 간격(초).

    Returns:
        전환 시점(초) 리스트. 길이는 항상 num_segments - 1 입니다.
    """
    if num_segments <= 1:
        return []

    # 비트 분석이 불가능할 때 사용할 균등 간격 타임스탬프
    fallback_times = [round((i + 1) * fallback_interval, 2) for i in range(num_segments - 1)]

    if not bgm_path or not Path(bgm_path).exists():
        logger.info("BGM이 없어 균등 간격으로 전환 지점을 배치합니다.")
        return fallback_times

    try:
        # librosa는 임포트가 무거워서 실제 분석이 필요할 때만 불러옵니다.
        import librosa

        logger.info("BGM 비트 분석 중: %s", bgm_path)
        # 비트 검출에는 고음질이 필요 없으므로 낮은 샘플레이트로 읽어 속도를 확보합니다.
        y, sr = librosa.load(bgm_path, sr=11025)

        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)

        if len(beat_times) == 0:
            logger.info("검출된 비트가 없어 균등 간격으로 대체합니다.")
            return fallback_times

        # librosa 버전에 따라 tempo가 numpy 스칼라로 오기도 합니다.
        tempo_float = float(tempo.item()) if hasattr(tempo, "item") else float(tempo)
        logger.info("BGM 템포: %.2f BPM, 총 비트 수: %d", tempo_float, len(beat_times))

        # min_interval 이상 떨어진 비트만 골라 전환 지점으로 삼습니다.
        selected_times: List[float] = []
        last_t = 0.0

        for t in beat_times:
            if t > last_t + min_interval:
                selected_times.append(round(float(t), 2))
                last_t = t
                if len(selected_times) == num_segments - 1:
                    break

        # 비트가 모자라면 마지막 지점부터 균등 간격으로 채웁니다.
        while len(selected_times) < num_segments - 1:
            last_t += fallback_interval
            selected_times.append(round(last_t, 2))

        logger.info("비트 동기화 전환 타임스탬프: %s", selected_times)
        return selected_times

    except Exception as exc:
        logger.warning("BGM 비트 분석 실패(%s). 균등 간격으로 대체합니다.", exc)
        return fallback_times
