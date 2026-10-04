"""
Spring 백엔드로 작업 결과를 통보하는 웹훅 전송 모듈.

이 서버는 요청을 받으면 즉시 202를 돌려주고 백그라운드에서 작업을 수행합니다.
따라서 결과는 HTTP 응답이 아니라 이 웹훅으로 전달됩니다.

웹훅 전송 실패는 예외를 삼킵니다. 이미 콘텐츠는 생성된 뒤이므로,
통보에 실패했다고 해서 작업 자체를 실패 처리하면 안 되기 때문입니다.
대신 결과가 사라지지 않도록 payload 를 `failed_webhooks/` 에 JSON 으로 보관합니다.
보관된 결과는 `python tools/resend_failed_webhooks.py` 로 다시 보낼 수 있습니다.
(자동 재시도는 하지 않습니다. 같은 콜백이 중복 도착할 수 있어 Spring 쪽 합의가 필요합니다.)
"""

import json
from typing import Optional

import requests

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# 작업 종류 식별자. Spring 쪽에서 콜백을 분기하는 데 사용합니다.
JOB_TYPE_GENERATE_CONTENT = "GENERATE_CONTENT"


def post_payload(payload: dict) -> Optional[str]:
    """
    payload 를 웹훅 URL 로 POST 합니다.

    Returns:
        성공(2xx)이면 None, 실패면 사람이 읽을 수 있는 실패 사유.
        예외를 던지지 않습니다.
    """
    try:
        response = requests.post(
            settings.SPRING_WEBHOOK_URL,
            json=payload,
            timeout=settings.WEBHOOK_TIMEOUT_SEC,
        )
    except Exception as exc:
        return f"전송 오류: {exc}"

    # 예전에는 응답 코드를 보지 않아, Spring 이 4xx/5xx 로 거절해도 성공처럼 지나갔습니다.
    if not response.ok:
        return f"HTTP {response.status_code}: {response.text[:200]}"
    return None


def _save_failed(payload: dict, task_id: str, reason: str) -> None:
    """전송에 실패한 payload 를 나중에 재전송할 수 있도록 디스크에 보관합니다."""
    try:
        settings.FAILED_WEBHOOKS_DIR.mkdir(parents=True, exist_ok=True)
        path = settings.FAILED_WEBHOOKS_DIR / f"{task_id}.json"
        path.write_text(
            json.dumps({"reason": reason, "payload": payload}, indent=2, ensure_ascii=False)
        )
        logger.error("[%s] 웹훅 결과를 보관했습니다: %s", task_id, path)
    except Exception as exc:
        logger.error("[%s] 웹훅 결과 보관도 실패: %s", task_id, exc)


def _send(payload: dict, task_id: str) -> None:
    """웹훅 payload를 POST 합니다. 실패해도 예외를 전파하지 않고 payload 를 보관합니다."""
    logger.info("[%s] 웹훅 전송 → %s", task_id, settings.SPRING_WEBHOOK_URL)
    logger.debug("[%s] 웹훅 payload:\n%s", task_id, json.dumps(payload, indent=2, ensure_ascii=False))

    failure = post_payload(payload)
    if failure is None:
        logger.info("[%s] 웹훅 전송 성공", task_id)
        return

    logger.error("[%s] 웹훅 전송 실패: %s", task_id, failure)
    _save_failed(payload, task_id, failure)


def send_success(task_id: str, schedule_id: Optional[str], data: dict) -> None:
    """
    작업 성공 결과를 전송합니다.

    Args:
        data: `ContentResult` 스키마 형태의 딕셔너리.
    """
    _send(
        {
            "taskId": task_id,
            "scheduleId": schedule_id,
            "status": "SUCCESS",
            "jobType": JOB_TYPE_GENERATE_CONTENT,
            "data": data,
        },
        task_id,
    )


def send_failure(task_id: str, schedule_id: Optional[str], error: str) -> None:
    """작업 실패를 전송합니다."""
    _send(
        {
            "taskId": task_id,
            "scheduleId": schedule_id,
            "status": "FAILED",
            "jobType": JOB_TYPE_GENERATE_CONTENT,
            "error": error,
        },
        task_id,
    )
