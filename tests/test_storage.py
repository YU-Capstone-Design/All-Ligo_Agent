"""S3 업로드 재시도 테스트 (boto3 클라이언트는 가짜)."""

import pytest
from botocore.exceptions import NoCredentialsError

from app.core.config import settings
from app.services import storage_service


class _FakeS3:
    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0

    def upload_file(self, *args, **kwargs):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)


@pytest.fixture
def fake_s3(monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(settings, "AWS_S3_BUCKET", "bucket")
    monkeypatch.setattr(settings, "AWS_S3_BASE_URL", "")
    monkeypatch.setattr(storage_service.time, "sleep", lambda sec: None)

    def install(errors):
        client = _FakeS3(errors)
        monkeypatch.setattr(storage_service.boto3, "client", lambda name: client)
        return client

    return str(video), install


def test_일시_실패는_한번_더_올려서_성공한다(fake_s3):
    path, install = fake_s3
    client = install([ConnectionError("reset")])

    url = storage_service.upload_video_to_s3(path, "content/v.mp4")

    assert client.calls == 2
    assert url.endswith("/content/v.mp4")


def test_두번_모두_실패하면_예외(fake_s3):
    path, install = fake_s3
    client = install([ConnectionError("a"), ConnectionError("b")])

    with pytest.raises(ConnectionError):
        storage_service.upload_video_to_s3(path, "content/v.mp4")
    assert client.calls == 2


def test_자격_증명이_없으면_재시도하지_않는다(fake_s3):
    path, install = fake_s3
    client = install([NoCredentialsError()])

    with pytest.raises(NoCredentialsError):
        storage_service.upload_video_to_s3(path, "content/v.mp4")
    assert client.calls == 1
