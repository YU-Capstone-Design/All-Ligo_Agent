"""
로깅 설정 모듈.

기존 코드는 전부 `print()` 를 사용했습니다. print 는 시간·모듈·심각도 정보가 없고
레벨별 필터링이나 파일 출력 전환도 불가능합니다. 운영 환경에서 로그를 추적하려면
표준 `logging` 모듈이 필요하므로, 서버 기동 시 1회 `setup_logging()` 을 호출합니다.

각 모듈에서는 아래처럼 모듈 전용 로거를 만들어 사용하세요.

    from app.core.logging_config import get_logger
    logger = get_logger(__name__)
"""

import logging
import sys

# 예) 2026-08-11 09:12:33 | INFO     | app.services.video_service | 영상 렌더링 시작
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: int = logging.INFO) -> None:
    """
    루트 로거에 stdout 핸들러를 연결합니다.

    uvicorn 이 자체 핸들러를 이미 붙여 두는 경우가 있어, 중복 출력을 막기 위해
    기존 핸들러를 제거한 뒤 새로 구성합니다.
    """
    root_logger = logging.getLogger()

    # 중복 핸들러로 같은 줄이 두 번 찍히는 것을 방지합니다.
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT))

    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # 외부 라이브러리의 과도한 DEBUG/INFO 로그를 억제합니다.
    for noisy in ("httpx", "httpcore", "urllib3", "botocore", "boto3", "s3transfer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """모듈 전용 로거를 반환합니다. `__name__` 을 그대로 넘겨서 사용하세요."""
    return logging.getLogger(name)
