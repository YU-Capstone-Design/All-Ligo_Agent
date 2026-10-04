"""
전송에 실패해 보관된 웹훅 결과를 Spring 으로 다시 보내는 수동 도구.

웹훅 전송이 실패하면(Spring 다운, 4xx/5xx 응답 등) 결과 payload 가
`failed_webhooks/<taskId>.json` 에 남습니다. Spring 이 복구된 뒤 이 스크립트로
다시 보내면 됩니다. 성공한 파일은 `failed_webhooks/sent/` 로 옮겨집니다.

같은 taskId 콜백이 Spring 에 두 번 도착할 수 있으니, Spring 쪽에서 이미 처리한
작업인지 확인한 뒤 실행하세요.

실행:
    python tools/resend_failed_webhooks.py            # 보관된 목록만 출력
    python tools/resend_failed_webhooks.py --send     # 전부 재전송
    python tools/resend_failed_webhooks.py --send <taskId>   # 특정 작업만 재전송
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.services.webhook_service import post_payload


def main() -> None:
    args = sys.argv[1:]
    send = "--send" in args
    targets = {a for a in args if not a.startswith("--")}

    files = sorted(settings.FAILED_WEBHOOKS_DIR.glob("*.json"))
    if targets:
        files = [f for f in files if f.stem in targets]

    if not files:
        print(f"보관된 웹훅이 없습니다: {settings.FAILED_WEBHOOKS_DIR}")
        return

    print(f"웹훅 대상: {settings.SPRING_WEBHOOK_URL}")
    sent_dir = settings.FAILED_WEBHOOKS_DIR / "sent"

    for path in files:
        record = json.loads(path.read_text())
        payload = record["payload"]
        print(f"- {path.stem}  status={payload.get('status')}  (실패 사유: {record.get('reason')})")

        if not send:
            continue

        failure = post_payload(payload)
        if failure is None:
            sent_dir.mkdir(parents=True, exist_ok=True)
            path.rename(sent_dir / path.name)
            print("  → 재전송 성공")
        else:
            print(f"  → 재전송 실패: {failure}")

    if not send:
        print("\n재전송하려면 --send 를 붙여 실행하세요.")


if __name__ == "__main__":
    main()
