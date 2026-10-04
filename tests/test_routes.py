"""API 라우트 테스트: 생성 요청 수락, 이미지 URL 검증, YouTube 업로드 경로 제한."""

from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.api.routes import marketing as marketing_route
from app.services import image_fetcher, youtube_service


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def fake_internet(monkeypatch):
    """image_fetcher 가 만드는 httpx 클라이언트를 가짜 응답으로 바꿉니다."""
    routes = {
        "/real.bin": httpx.Response(200, content=_png_bytes(),
                                    headers={"content-type": "application/octet-stream"}),
        "/error.png": httpx.Response(200, content=b"<html>AccessDenied</html>"),
        "/big.png": httpx.Response(200, content=b"0" * (image_fetcher._MAX_IMAGE_BYTES + 1)),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(request.url.path, httpx.Response(404))

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        image_fetcher.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )


@pytest.fixture
def no_background_job(monkeypatch):
    """202 응답까지만 확인하고 실제 생성 파이프라인은 돌리지 않습니다."""
    calls = []

    async def fake_run(req):
        calls.append(req)

    monkeypatch.setattr(marketing_route, "run_content_generation", fake_run)
    monkeypatch.setattr(marketing_route.weather_service, "build_weather_context", lambda lat, lon: "맑음")
    return calls


def test_generate_는_즉시_202와_taskId를_반환한다(client, isolated_static, no_background_job):
    res = client.post("/api/marketing/generate", json={"contentType": "VIDEO", "mode": "TRANSFORM", "scheduleId": 7})

    assert res.status_code == 202
    assert res.json()["status"] == "PROCESSING"
    assert no_background_job[0].task_id == res.json()["taskId"]
    assert no_background_job[0].schedule_id == "7"


@pytest.mark.parametrize("url, message", [
    ("ftp://example.com/a.png", "http/https"),
    ("http://img.test/error.png", "이미지로 읽을 수 없는"),
    ("http://img.test/none.png", "다운로드 실패"),
    ("http://img.test/big.png", "너무 큽니다"),
])
def test_잘못된_이미지_URL은_400(client, isolated_static, fake_internet, no_background_job, url, message):
    res = client.post("/api/marketing/generate", json={"imageUrls": [url]})

    assert res.status_code == 400
    assert message in res.json()["detail"]
    assert no_background_job == []


def test_일부만_실패해도_400이고_받은_파일은_지운다(client, isolated_static, fake_internet, no_background_job):
    urls = ["http://img.test/real.bin", "http://img.test/error.png"]
    res = client.post("/api/marketing/generate", json={"imageUrls": urls})

    assert res.status_code == 400
    assert list((isolated_static / "static" / "uploads").iterdir()) == []


def test_정상_이미지는_실제_포맷_확장자로_저장된다(client, isolated_static, fake_internet, no_background_job):
    res = client.post("/api/marketing/generate", json={"imageUrls": ["http://img.test/real.bin"]})

    assert res.status_code == 202
    saved = no_background_job[0].saved_image_paths
    assert len(saved) == 1 and saved[0].suffix == ".png" and saved[0].exists()


@pytest.fixture
def fake_youtube(monkeypatch):
    uploaded = []

    def fake_upload(path, title, description, tags):
        uploaded.append(path)
        return "https://youtube.com/shorts/test"

    monkeypatch.setattr(youtube_service, "upload_video", fake_upload)
    return uploaded


def _upload(client, path):
    return client.post("/api/marketing/upload", json={"localVideoPath": path, "title": "t", "description": "d"})


def test_upload_static_videos_밖의_경로는_400(client, isolated_static, fake_youtube, tmp_path):
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"x")
    link = isolated_static / "static" / "videos" / "link.mp4"
    link.symlink_to(outside)

    for path in (str(outside), "static/videos/../../outside.mp4", "static/videos/link.mp4"):
        res = _upload(client, path)
        assert res.status_code == 400, path

    assert fake_youtube == []


def test_upload_없는_파일은_404_빈_파일은_400(client, isolated_static, fake_youtube):
    (isolated_static / "static" / "videos" / "empty.mp4").write_bytes(b"")

    assert _upload(client, "static/videos/none.mp4").status_code == 404
    assert _upload(client, "static/videos/empty.mp4").status_code == 400


def test_upload_정상_경로는_업로드된다(client, isolated_static, fake_youtube):
    (isolated_static / "static" / "videos" / "ok.mp4").write_bytes(b"video")

    res = _upload(client, "static/videos/ok.mp4")

    assert res.status_code == 200
    assert res.json()["youtubeUrl"] == "https://youtube.com/shorts/test"
    assert len(fake_youtube) == 1


def test_upload_같은_영상을_다시_요청하면_다시_올리지_않고_기존_URL(client, isolated_static, fake_youtube):
    (isolated_static / "static" / "videos" / "ok.mp4").write_bytes(b"video")

    first = _upload(client, "static/videos/ok.mp4")
    # Was 재시도처럼 같은 영상을 다른 표기(절대 경로)로 다시 요청
    second = _upload(client, str(isolated_static / "static" / "videos" / "ok.mp4"))

    assert first.status_code == second.status_code == 200
    assert first.json()["youtubeUrl"] == second.json()["youtubeUrl"]
    assert len(fake_youtube) == 1
