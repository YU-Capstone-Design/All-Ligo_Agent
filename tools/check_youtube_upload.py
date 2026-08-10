"""
YouTube 업로드 연동 확인용 수동 점검 스크립트.

지정한 로컬 영상 파일을 실제로 업로드해 봅니다.
(자동화된 테스트가 아니라 개발 중 손으로 돌려보는 도구입니다.
 실행하면 진짜로 YouTube에 영상이 올라가니 주의하세요.)

실행:
    python tools/check_youtube_upload.py static/videos/shortform_xxx.mp4
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.logging_config import setup_logging
from app.services import youtube_service

TITLE = "All-Ligo Test Video #shorts"
DESCRIPTION = "This is a test video uploaded automatically by All-Ligo Marketing AI Agent."
TAGS = ["test", "shortform", "allligo"]


def main() -> None:
    setup_logging()

    if len(sys.argv) < 2:
        print("사용법: python tools/check_youtube_upload.py <영상 파일 경로>")
        sys.exit(1)

    video_path = Path(sys.argv[1])
    if not video_path.exists():
        print(f"오류: 영상 파일을 찾을 수 없습니다: {video_path}")
        sys.exit(1)

    print(f"업로드 테스트 시작: {video_path}")
    try:
        url = youtube_service.upload_video(str(video_path), TITLE, DESCRIPTION, TAGS)
        print("\n" + "=" * 50)
        print(f"성공! YouTube URL: {url}")
        print("=" * 50)
    except Exception as exc:
        print("\n" + "=" * 50)
        print(f"업로드 실패: {exc}")
        print("=" * 50)
        sys.exit(1)


if __name__ == "__main__":
    main()
