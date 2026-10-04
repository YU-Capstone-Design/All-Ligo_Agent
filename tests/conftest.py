"""
공용 테스트 픽스처.

이 테스트들은 GPU·Ollama·S3·YouTube·Spring 없이 돌아가는 스모크 테스트입니다.
외부 호출은 전부 monkeypatch 로 가짜 함수로 바꿉니다.

실행:
    pip install -r requirements-dev.txt
    pytest
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


@pytest.fixture
def client() -> TestClient:
    # `with` 없이 만들면 lifespan(기동 점검 스레드 등)이 실행되지 않습니다.
    return TestClient(app)


@pytest.fixture
def isolated_static(tmp_path, monkeypatch):
    """static/ 하위 경로를 임시 폴더로 바꿔, 테스트가 실제 산출물 폴더를 건드리지 않게 합니다."""
    static = tmp_path / "static"
    for name in ("images", "videos", "uploads"):
        (static / name).mkdir(parents=True)
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "STATIC_DIR", static)
    monkeypatch.setattr(settings, "IMAGES_DIR", static / "images")
    monkeypatch.setattr(settings, "VIDEOS_DIR", static / "videos")
    monkeypatch.setattr(settings, "UPLOADS_DIR", static / "uploads")
    monkeypatch.setattr(settings, "FAILED_WEBHOOKS_DIR", tmp_path / "failed_webhooks")
    return tmp_path
