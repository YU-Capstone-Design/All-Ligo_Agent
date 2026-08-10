"""
AWS S3 업로드 서비스. (기존 s3_uploader.py)

생성된 영상을 S3에 올리고 공개 URL을 반환합니다.
자격 증명은 표준 boto3 체인(환경 변수, ~/.aws/credentials, IAM 역할 등)을 따릅니다.

필요한 환경 변수:
    AWS_ACCESS_KEY_ID     - (boto3 표준)
    AWS_SECRET_ACCESS_KEY - (boto3 표준)
    AWS_REGION            - 기본값 ap-northeast-2
    AWS_S3_BUCKET         - 필수
    AWS_S3_BASE_URL       - 선택. CloudFront 등 커스텀 도메인을 쓸 때 지정
"""

from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


def _build_public_url(bucket: str, region: str, object_name: str) -> str:
    """
    업로드된 객체의 공개 URL을 만듭니다.

    커스텀 base URL이 설정되어 있으면 그것을 우선 사용하고,
    없으면 리전별 표준 S3 엔드포인트 형식을 사용합니다.
    (us-east-1만 리전 표기가 빠진 예외 형식을 씁니다.)
    """
    if settings.AWS_S3_BASE_URL:
        return f"{settings.AWS_S3_BASE_URL.rstrip('/')}/{object_name}"
    if region == "us-east-1":
        return f"https://{bucket}.s3.amazonaws.com/{object_name}"
    return f"https://{bucket}.s3.{region}.amazonaws.com/{object_name}"


def upload_video_to_s3(file_path: str, object_name: Optional[str] = None) -> str:
    """
    영상 파일을 S3에 업로드하고 공개 URL을 반환합니다.

    이 함수는 **동기(blocking)** 입니다. 코루틴에서는 `run_in_threadpool()` 로 감싸세요.

    Args:
        file_path: 업로드할 로컬 파일 경로.
        object_name: S3 객체 키. 생략하면 파일명을 그대로 사용합니다.

    Raises:
        ValueError: AWS_S3_BUCKET 환경 변수가 없을 때.
        FileNotFoundError / NoCredentialsError / ClientError: boto3 업로드 실패 시.
    """
    bucket_name = settings.AWS_S3_BUCKET
    region_name = settings.AWS_REGION

    if not bucket_name:
        raise ValueError("[S3] AWS_S3_BUCKET 환경 변수가 설정되지 않았습니다.")

    if object_name is None:
        object_name = Path(file_path).name

    s3_client = boto3.client("s3")

    try:
        logger.info("S3 업로드 시작: %s → s3://%s/%s", file_path, bucket_name, object_name)
        s3_client.upload_file(
            file_path,
            bucket_name,
            object_name,
            # ContentType을 지정하지 않으면 브라우저가 다운로드로 처리합니다.
            ExtraArgs={"ContentType": "video/mp4"},
        )

        s3_url = _build_public_url(bucket_name, region_name, object_name)
        logger.info("S3 업로드 성공: %s", s3_url)
        return s3_url

    except FileNotFoundError:
        logger.error("S3 업로드 실패 - 파일을 찾을 수 없음: %s", file_path)
        raise
    except NoCredentialsError:
        logger.error("S3 업로드 실패 - AWS 자격 증명을 찾을 수 없습니다.")
        raise
    except ClientError as exc:
        logger.error("S3 업로드 실패 - AWS ClientError: %s", exc)
        raise
