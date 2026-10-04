"""
Spring 백엔드로 작업 결과를 통보하는 웹훅 전송 모듈.

이 서버는 요청을 받으면 즉시 202를 돌려주고 백그라운드에서 작업을 수행합니다.
따라서 결과는 HTTP 응답이 아니라 이 웹훅으로 전달됩니다.

웹훅 전송 실패는 예외를 삼킵니다. 이미 콘텐츠는 생성된 뒤이므로,
통보에 실패했다고 해서 작업 자체를 실패 처리하면 안 되기 때문입니다.

일시적인 실패(연결 오류, 타임아웃, 5xx)는 잠시 기다렸다가 자동으로 다시 보냅니다.
Was 의 콜백 처리는 같은 taskId 를 두 번 받아도 결과가 같으므로(Content·알림 중복 없음,
`docs/WAS_INTEGRATION.md` §3) 재전송으로 인한 중복은 문제가 되지 않습니다.
끝내 실패하면 결과가 사라지지 않도록 payload 를 `failed_webhooks/` 에 JSON 으로 보관하고,
`python tools/resend_failed_webhooks.py` 로 다시 보낼 수 있습니다.
"""

import json
import time
from typing import Optional, Tuple

import requests

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# 작업 종류 식별자. Spring 쪽에서 콜백을 분기하는 데 사용합니다.
JOB_TYPE_GENERATE_CONTENT = "GENERATE_CONTENT"

# 재시도 사이 대기 시간(초). 시도 횟수가 더 많으면 마지막 값을 반복합니다.
_RETRY_DELAYS_SEC = (2, 5, 10)


# 재시도할 HTTP 상태 코드: 5xx 전부(Cloudflare 52x 포함) + 요청 타임아웃/과다 요청
_RETRYABLE_STATUS = {408, 429}


def _post_once(payload: dict) -> Tuple[Optional[str], bool]:
    """
    payload 를 한 번 POST 합니다.

    Returns:
        (실패 사유, 재시도할 가치가 있는지). 성공(2xx)이면 (None, False).
    """
    try:
        response = requests.post(
            settings.SPRING_WEBHOOK_URL,
            json=payload,
            timeout=settings.WEBHOOK_TIMEOUT_SEC,
        )
    except Exception as exc:
        # 연결 거부, DNS 실패, 타임아웃 등은 Spring 이 잠깐 내려가 있을 때 흔합니다.
        return f"전송 오류: {exc}", True

    # 예전에는 응답 코드를 보지 않아, Spring 이 4xx/5xx 로 거절해도 성공처럼 지나갔습니다.
    if response.ok:
        return None, False
    retryable = response.status_code >= 500 or response.status_code in _RETRYABLE_STATUS
    return f"HTTP {response.status_code}: {response.text[:200]}", retryable


def post_payload(payload: dict) -> Optional[str]:
    """
    payload 를 웹훅 URL 로 POST 하고, 일시적인 실패면 정해진 횟수만큼 다시 보냅니다.

    4xx(잘못된 요청) 는 다시 보내도 같은 결과이므로 재시도하지 않습니다.

    Returns:
        성공(2xx)이면 None, 끝내 실패하면 마지막 실패 사유.
        예외를 던지지 않습니다.
    """
    attempts = max(1, settings.WEBHOOK_MAX_ATTEMPTS)
    failure: Optional[str] = None

    for attempt in range(1, attempts + 1):
        failure, retryable = _post_once(payload)
        if failure is None:
            return None
        if not retryable or attempt == attempts:
            break

        delay = _RETRY_DELAYS_SEC[min(attempt - 1, len(_RETRY_DELAYS_SEC) - 1)]
        logger.warning(
            "웹훅 전송 실패(%s). %d초 뒤 다시 보냅니다 (%d/%d)", failure, delay, attempt, attempts
        )
        time.sleep(delay)

    return failure


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
