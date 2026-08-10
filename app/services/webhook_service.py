"""
Spring 백엔드로 작업 결과를 통보하는 웹훅 전송 모듈.

이 서버는 요청을 받으면 즉시 202를 돌려주고 백그라운드에서 작업을 수행합니다.
따라서 결과는 HTTP 응답이 아니라 이 웹훅으로 전달됩니다.

웹훅 전송 실패는 로그만 남기고 예외를 삼킵니다. 이미 콘텐츠는 생성된 뒤이므로,
통보에 실패했다고 해서 작업 자체를 실패 처리하면 안 되기 때문입니다.
"""

import json
from typing import Optional

import requests

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# 작업 종류 식별자. Spring 쪽에서 콜백을 분기하는 데 사용합니다.
JOB_TYPE_GENERATE_CONTENT = "GENERATE_CONTENT"


def _send(payload: dict, task_id: str) -> None:
    """웹훅 payload를 POST 합니다. 실패해도 예외를 전파하지 않습니다."""
    logger.info("[%s] 웹훅 전송 → %s", task_id, settings.SPRING_WEBHOOK_URL)
    logger.debug("[%s] 웹훅 payload:\n%s", task_id, json.dumps(payload, indent=2, ensure_ascii=False))

    try:
        requests.post(
            settings.SPRING_WEBHOOK_URL,
            json=payload,
            timeout=settings.WEBHOOK_TIMEOUT_SEC,
        )
    except Exception as exc:
        logger.error("[%s] 웹훅 전송 실패: %s", task_id, exc)


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
