"""YouTube 중복 업로드 방지 테스트 (실제 업로드는 가짜)."""

import threading
import time

import pytest

from app.services import youtube_service


@pytest.fixture
def video(isolated_static):
    path = isolated_static / "static" / "videos" / "v.mp4"
    path.write_bytes(b"video")
    return str(path)


def test_동시에_같은_영상_업로드_요청이_와도_한번만_올린다(video, monkeypatch):
    calls = []

    def slow_upload(path, title, description, tags):
        calls.append(path)
        time.sleep(0.3)           # 업로드 중에 두 번째 요청이 도착
        return "https://youtube.com/shorts/once"

    monkeypatch.setattr(youtube_service, "upload_video", slow_upload)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(youtube_service.upload_video_once(video, "t", "d")))
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1
    assert results == ["https://youtube.com/shorts/once"] * 2


def test_업로드가_실패하면_기록하지_않아_다음_요청에서_다시_올린다(video, monkeypatch):
    attempts = []

    def flaky_upload(path, title, description, tags):
        attempts.append(path)
        if len(attempts) == 1:
            raise RuntimeError("YouTube 5xx")
        return "https://youtube.com/shorts/retry"

    monkeypatch.setattr(youtube_service, "upload_video", flaky_upload)

    with pytest.raises(RuntimeError):
        youtube_service.upload_video_once(video, "t", "d")
    assert youtube_service.find_uploaded_url(video) is None

    assert youtube_service.upload_video_once(video, "t", "d") == "https://youtube.com/shorts/retry"
    assert len(attempts) == 2
