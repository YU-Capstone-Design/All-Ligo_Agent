"""
서버 런타임 상태(현재 실행 중인 백그라운드 작업 수) 추적 모듈.

기존에는 main.py 의 전역 변수 `active_jobs_count` 를 여러 함수가 `global` 선언과
함께 직접 증감시켰습니다. 전역 변수는 누가 언제 바꾸는지 추적이 어렵고,
증감 로직을 빠뜨리면 카운터가 영구히 어긋납니다.

여기서는 작은 트래커 객체로 감싸고, 컨텍스트 매니저를 제공해서
`with job_tracker.track(): ...` 블록을 벗어나면 예외가 나든 말든
반드시 카운터가 복구되도록 보장합니다.
"""

import threading
from contextlib import contextmanager
from typing import Iterator


class JobTracker:
    """실행 중인 백그라운드 AI 생성 작업 수를 세는 스레드 안전 카운터."""

    def __init__(self) -> None:
        self._count = 0
        # 작업이 스레드풀(run_in_threadpool)과 이벤트 루프를 오가므로 락으로 보호합니다.
        self._lock = threading.Lock()

    @property
    def active_count(self) -> int:
        """현재 실행 중인 작업 수."""
        with self._lock:
            return self._count

    @contextmanager
    def track(self) -> Iterator[None]:
        """
        작업 1건의 수명을 감싸는 컨텍스트 매니저.

        블록 진입 시 카운터를 올리고, 블록을 벗어날 때(정상 종료·예외 무관)
        반드시 내립니다.
        """
        with self._lock:
            self._count += 1
        try:
            yield
        finally:
            with self._lock:
                self._count -= 1


# 애플리케이션 전역에서 공유하는 트래커 인스턴스
job_tracker = JobTracker()
