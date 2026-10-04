"""생성 파이프라인 분기와 웹훅 전송 테스트 (외부 서비스는 전부 가짜로 대체)."""

import asyncio
import json

import pytest

from app.core.state import job_tracker
from app.services import content_pipeline as cp
from app.services import preflight_service, webhook_service

LLM_VIDEO_RESPONSE = (
    "가을엔 밤라떼 ✨\n"
    "[IMAGE_PROMPT_1]: a latte\n[IMAGE_PROMPT_2]: a cafe\n[IMAGE_PROMPT_3]: autumn leaves"
)


@pytest.fixture
def sent(monkeypatch):
    """웹훅 전송을 가로채 payload 를 모읍니다."""
    payloads = []
    monkeypatch.setattr(webhook_service, "_send", lambda payload, task_id: payloads.append(payload))
    return payloads


@pytest.fixture
def fake_ai(monkeypatch):
    """LLM·이미지·영상·S3 를 가짜로 대체하고 호출 기록을 돌려줍니다."""
    calls = {"llm": 0, "images": [], "video": []}
    responses = [LLM_VIDEO_RESPONSE]

    async def fake_llm(req, mode, vision):
        calls["llm"] += 1
        return responses[min(calls["llm"], len(responses)) - 1]

    def fake_images(prompts):
        calls["images"].append(prompts)
        return [f"poster_{i}.png" for i in range(len(prompts))]

    def fake_video(paths, text):
        calls["video"].append((paths, text))
        return "shortform_test.mp4"

    monkeypatch.setattr(cp, "_generate_marketing_text", fake_llm)
    monkeypatch.setattr(cp.image_service, "generate_images", fake_images)
    monkeypatch.setattr(cp, "create_shortform_video", fake_video)
    monkeypatch.setattr(cp.storage_service, "upload_video_to_s3", lambda path, key: f"https://s3.test/{key}")
    calls["responses"] = responses
    return calls


def _request(**overrides) -> cp.ContentRequest:
    values = dict(
        task_id="task-1", base_url="https://az.test", content_type="VIDEO", mode="TRANSFORM",
        saved_image_paths=[], schedule_id="42", mood_tag="", hash_tag="", user_prompt="",
        upload_day="월요일", upload_time="18:00", weather_data="",
    )
    values.update(overrides)
    return cp.ContentRequest(**values)


def test_VIDEO_TRANSFORM_성공_웹훅(isolated_static, sent, fake_ai):
    asyncio.run(cp.run_content_generation(_request()))

    assert len(sent) == 1
    payload = sent[0]
    assert payload["status"] == "SUCCESS" and payload["scheduleId"] == "42"
    data = payload["data"]
    assert data["generatedText"] == "가을엔 밤라떼 ✨"     # 웹훅 문구는 이모지를 유지
    assert data["posterUrl"] == "https://az.test/static/images/poster_0.png"
    assert data["s3VideoUrl"] == "https://s3.test/content/shortform_test.mp4"
    assert data["localVideoPath"] == "static/videos/shortform_test.mp4"
    assert fake_ai["images"] == [["a latte", "a cafe", "autumn leaves"]]   # 모델 1회 로드로 3장
    assert job_tracker.active_count == 0


def test_POST는_영상을_만들지_않는다(isolated_static, sent, fake_ai):
    fake_ai["responses"][0] = "포스트 본문"
    asyncio.run(cp.run_content_generation(_request(content_type="POST")))

    data = sent[0]["data"]
    assert sent[0]["status"] == "SUCCESS"
    assert data["s3VideoUrl"] is None and data["localVideoPath"] is None
    assert fake_ai["images"] == [] and fake_ai["video"] == []


def test_이미지_프롬프트가_빠지면_한번_재생성한다(isolated_static, sent, fake_ai):
    fake_ai["responses"][:] = ["본문만 있음", LLM_VIDEO_RESPONSE]
    asyncio.run(cp.run_content_generation(_request()))

    assert fake_ai["llm"] == 2
    assert sent[0]["status"] == "SUCCESS" and sent[0]["data"]["localVideoPath"]


def test_LLM이_빈_응답이면_FAILED(isolated_static, sent, fake_ai):
    fake_ai["responses"][:] = ["", "   "]
    asyncio.run(cp.run_content_generation(_request()))

    assert sent[0]["status"] == "FAILED"
    assert "빈 응답" in sent[0]["error"]
    assert job_tracker.active_count == 0


def test_어느_단계든_예외가_나면_FAILED_웹훅을_보내고_임시파일을_지운다(isolated_static, sent, fake_ai, monkeypatch):
    upload = isolated_static / "static" / "uploads" / "upload.png"
    upload.write_bytes(b"x")

    async def boom(*args):
        raise ConnectionError("Ollama 연결 실패")

    monkeypatch.setattr(cp, "_generate_text_with_retry", boom)
    asyncio.run(cp.run_content_generation(_request(saved_image_paths=[])))
    asyncio.run(cp.run_content_generation(_request(content_type="POST", mode="TRANSFORM",
                                                   saved_image_paths=[upload])))

    assert [p["status"] for p in sent] == ["FAILED", "FAILED"]
    assert not upload.exists()


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = "server error"


def test_웹훅이_2xx가_아니면_payload를_보관한다(isolated_static, monkeypatch):
    monkeypatch.setattr(webhook_service.requests, "post", lambda *a, **k: _FakeResponse(500))

    webhook_service.send_failure("task-9", "1", "에러")

    saved = isolated_static / "failed_webhooks" / "task-9.json"
    record = json.loads(saved.read_text())
    assert record["reason"].startswith("HTTP 500")
    assert record["payload"]["taskId"] == "task-9"


def test_웹훅_성공이면_보관하지_않는다(isolated_static, monkeypatch):
    monkeypatch.setattr(webhook_service.requests, "post", lambda *a, **k: _FakeResponse(200))

    webhook_service.send_failure("task-10", "1", "에러")

    assert not (isolated_static / "failed_webhooks").exists()


def test_기동_점검은_개별_항목이_예외를_던져도_결과를_돌려준다(monkeypatch):
    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(preflight_service, "_CHECKS", [broken, preflight_service._check_webhook])

    report = preflight_service.run_preflight()

    assert [c.ok for c in report.checks] == [False, True]
    assert report.ok   # 경고(warning)만 있으면 전체는 ok
