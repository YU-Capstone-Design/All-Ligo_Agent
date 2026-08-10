"""
Ollama Vision 모델 연결 확인용 수동 점검 스크립트.

1x1 크기의 더미 이미지를 보내 Ollama가 응답하는지만 확인합니다.
(자동화된 테스트가 아니라 개발 중 손으로 돌려보는 도구입니다.)

실행:
    python tools/check_ollama.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from app.core.config import settings

# 1x1 검은 픽셀 PNG (base64)
DUMMY_IMAGE_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def main() -> None:
    payload = {
        "model": settings.OLLAMA_VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": "What is in this image?",
                "images": [DUMMY_IMAGE_B64],
            }
        ],
        "stream": False,
    }

    url = f"{settings.OLLAMA_BASE_URL}/api/chat"
    print(f"요청 전송: {url} (model={settings.OLLAMA_VISION_MODEL})")

    response = requests.post(url, json=payload, timeout=30)
    print(f"상태 코드: {response.status_code}")
    print(response.json())


if __name__ == "__main__":
    main()
