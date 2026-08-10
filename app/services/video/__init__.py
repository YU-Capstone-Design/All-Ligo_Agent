"""
숏폼 영상 생성 패키지. (기존 video_generator.py)

Instagram Reels / YouTube Shorts 품질의 광고 영상을 이미지로부터 생성합니다.
배경음악(BGM)의 비트 리듬에 맞춰 화면을 전환하고, 타이핑 자막 효과를 적용합니다.

외부에서는 이 패키지의 공개 API만 사용하세요. 내부 모듈 구성은 다음과 같습니다.

    constants.py     - 해상도, 폰트, Ken Burns 프리셋 등 상수
    ffmpeg_runner.py - FFmpeg 실행 및 NVENC → CPU 폴백
    audio.py         - TTS 나레이션, BGM 선택, 비트 분석
    subtitles.py     - PIL 기반 타이핑 자막 PNG 시퀀스 생성
    compositor.py    - 이미지 전처리 / 세그먼트 / 트랜지션 / 최종 합성
    renderer.py      - 위 단계들을 조립하는 오케스트레이터
"""

from .renderer import create_shortform_video, generate_video_from_local

__all__ = ["create_shortform_video", "generate_video_from_local"]
