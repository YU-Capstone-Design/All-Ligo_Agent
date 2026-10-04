"""
기동 전 점검(preflight) 서비스.

콘텐츠 생성은 Ollama, 이미지 모델 캐시, FFmpeg, TTS, 폰트, S3, YouTube 토큰 등
외부 요소에 많이 기대고 있습니다. 이 중 하나가 빠져 있으면 요청이 들어온 뒤
한참 지나서야(또는 조용히) 실패하므로, 서버 기동 시 한 번 점검해서 로그에 요약하고
`/api/system/preflight` 로도 언제든 다시 확인할 수 있게 합니다.

점검은 읽기 전용입니다. 어떤 파일도 바꾸지 않습니다.
(YouTube 토큰은 메모리 안에서만 갱신을 시도하고 token.json 에 저장하지 않습니다.)
"""

import importlib.util
import json
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import requests

from app.core.config import settings
from app.core.logging_config import get_logger
from app.schemas.system import PreflightCheck, PreflightResponse

logger = get_logger(__name__)

_OLLAMA_TIMEOUT_SEC = 3

ERROR = "error"      # 해당 기능이 동작하지 않음
WARNING = "warning"  # 동작은 하지만 품질/일부 기능이 떨어짐


def _ok(name: str, detail: str) -> PreflightCheck:
    return PreflightCheck(name=name, ok=True, severity="ok", detail=detail)


def _fail(name: str, severity: str, detail: str) -> PreflightCheck:
    return PreflightCheck(name=name, ok=False, severity=severity, detail=detail)


def _check_binaries() -> List[PreflightCheck]:
    checks = []
    for binary in ("ffmpeg", "ffprobe"):
        path = shutil.which(binary)
        checks.append(
            _ok(binary, path) if path
            else _fail(binary, ERROR, "설치되어 있지 않습니다. 영상 생성 불가 (sudo apt install ffmpeg)")
        )
    return checks


def _check_tts() -> PreflightCheck:
    if importlib.util.find_spec("edge_tts") is None:
        return _fail("edge-tts", WARNING, "edge_tts 패키지가 없습니다. 영상에 나레이션이 빠집니다")
    return _ok("edge-tts", "설치됨 (Microsoft 온라인 서비스 사용 — 인터넷 필요)")


def _check_ollama() -> List[PreflightCheck]:
    try:
        response = requests.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=_OLLAMA_TIMEOUT_SEC)
        response.raise_for_status()
        installed = {m.get("name") for m in response.json().get("models", [])}
    except Exception as exc:
        return [_fail("ollama", ERROR, f"{settings.OLLAMA_BASE_URL} 에 연결할 수 없습니다: {exc}")]

    checks = [_ok("ollama", settings.OLLAMA_BASE_URL)]
    for label, model, severity in (
        ("ollama 텍스트 모델", settings.OLLAMA_TEXT_MODEL, ERROR),
        ("ollama 비전 모델", settings.OLLAMA_VISION_MODEL, WARNING),
    ):
        if model in installed:
            checks.append(_ok(label, model))
        else:
            checks.append(_fail(label, severity, f"{model} 이 설치되어 있지 않습니다 (ollama pull {model})"))
    return checks


def _check_image_models() -> List[PreflightCheck]:
    from huggingface_hub import try_to_load_from_cache

    from app.services.image_service import FLUX_MODEL_ID, SDXL_MODEL_ID

    def cached(repo_id: str) -> bool:
        return isinstance(try_to_load_from_cache(repo_id, "model_index.json"), str)

    has_flux, has_sdxl = cached(FLUX_MODEL_ID), cached(SDXL_MODEL_ID)
    download_note = " (IMAGE_MODEL_ALLOW_DOWNLOAD=true 면 첫 요청 때 내려받음)" \
        if settings.IMAGE_MODEL_ALLOW_DOWNLOAD else ""

    if has_flux:
        return [_ok("이미지 모델", f"FLUX.1-schnell 사용 (SDXL 캐시={'있음' if has_sdxl else '없음'})")]
    if has_sdxl:
        return [_fail("이미지 모델", WARNING, "FLUX 캐시 없음 → SDXL 로 생성 (더 느림)" + download_note)]
    return [_fail("이미지 모델", ERROR, "FLUX/SDXL 캐시가 모두 없습니다. AI 이미지 생성 불가" + download_note)]


def _check_assets() -> List[PreflightCheck]:
    from app.services.video.constants import FONT_BOLD, FONT_FALLBACK

    checks = []
    if FONT_BOLD.exists():
        checks.append(_ok("자막 폰트", FONT_BOLD.name))
    elif Path(FONT_FALLBACK).exists():
        checks.append(_fail("자막 폰트", WARNING, f"Pretendard 없음 → 대체 폰트 사용: {FONT_FALLBACK}"))
    else:
        checks.append(_fail("자막 폰트", ERROR, "자막 폰트가 없습니다. 영상 생성 실패 (fonts/Pretendard-ExtraBold.otf)"))

    bgm_count = len(list(settings.BGM_DIR.glob("*.mp3"))) if settings.BGM_DIR.exists() else 0
    checks.append(
        _ok("BGM", f"{bgm_count}곡") if bgm_count
        else _fail("BGM", WARNING, f"{settings.BGM_DIR} 에 mp3 가 없습니다. 영상이 BGM 없이 생성됩니다")
    )

    free_mb = shutil.disk_usage(settings.STATIC_DIR).free / (1024 * 1024)
    checks.append(
        _ok("디스크", f"여유 {free_mb / 1024:.1f}GB") if free_mb > settings.MIN_FREE_DISK_MB
        else _fail("디스크", ERROR, f"여유 공간 부족: {free_mb:.0f}MB")
    )
    return checks


def _check_s3() -> PreflightCheck:
    if not settings.AWS_S3_BUCKET:
        return _fail("S3", WARNING, "AWS_S3_BUCKET 미설정 → 영상이 S3 에 올라가지 않음 (s3VideoUrl=null)")

    import boto3

    if boto3.session.Session().get_credentials() is None:
        return _fail("S3", WARNING, "AWS 자격 증명이 없습니다 → S3 업로드 실패")
    # 실제 업로드 권한까지는 확인하지 않습니다(버킷에 쓰기가 발생하므로).
    return _ok("S3", "버킷 설정됨, 자격 증명 있음 (업로드 권한은 미확인)")


def youtube_expiry_note(now: Optional[datetime] = None) -> Optional[Tuple[str, str]]:
    """
    토큰 발급 기록에 refresh token 만료 시각이 있으면 (등급, 설명) 을 반환합니다. 없으면 None.

    만료 시각이 기록돼 있다는 것은 OAuth 동의 화면이 "테스트" 상태라 7일짜리 토큰이
    발급됐다는 뜻이므로, 기한이 남아 있어도 경고로 알립니다.
    """
    try:
        meta = json.loads(settings.YOUTUBE_TOKEN_META_FILE.read_text())
    except Exception:
        return None
    expires_at = meta.get("refreshTokenExpiresAt")
    if not expires_at:
        return None

    remaining = datetime.fromisoformat(expires_at) - (now or datetime.now())
    if remaining.total_seconds() <= 0:
        return ERROR, f"refresh token 만료됨({expires_at}) → 재발급 필요"
    days = remaining.total_seconds() / 86400
    return WARNING, (
        f"refresh token 이 {days:.1f}일 뒤 만료({expires_at}). "
        "동의 화면이 '테스트' 상태 → '프로덕션' 게시 후 재발급 필요"
    )


def _check_youtube() -> PreflightCheck:
    token_file = settings.YOUTUBE_TOKEN_FILE
    if not token_file.exists():
        return _fail("YouTube 토큰", WARNING, "token.json 없음 → /api/marketing/upload 불가")

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    try:
        creds = Credentials.from_authorized_user_file(str(token_file), list(settings.YOUTUBE_SCOPES))
        if not creds.refresh_token:
            return _fail("YouTube 토큰", WARNING, "refresh_token 없음 → 재발급 필요")
        # 메모리 안에서만 갱신을 시도합니다. 파일에는 쓰지 않습니다.
        # (access token 이 아직 유효해도 refresh token 이 살아 있는지 보려고 항상 갱신해 봅니다)
        creds.refresh(Request())
    except Exception as exc:
        return _fail(
            "YouTube 토큰", WARNING,
            f"갱신 실패({exc.__class__.__name__}) → python scripts/refresh_youtube_token_manual.py 로 재발급 필요",
        )

    note = youtube_expiry_note()
    if note:
        return _fail("YouTube 토큰", note[0], note[1])
    return _ok("YouTube 토큰", "refresh_token 유효")


def _check_public_url() -> PreflightCheck:
    if settings.AGENT_PUBLIC_BASE_URL:
        return _ok("공개 주소", f"{settings.AGENT_PUBLIC_BASE_URL} (posterUrl 기준)")
    return _fail("공개 주소", WARNING, "AGENT_PUBLIC_BASE_URL 미설정 → posterUrl 이 Was 가 호출한 주소(http/https)를 따라감")


def _check_webhook() -> PreflightCheck:
    # 실제 POST 는 하지 않습니다(Spring 에 빈 콜백이 쌓이므로). 설정값만 보여줍니다.
    return _ok("웹훅 대상", settings.SPRING_WEBHOOK_URL)


_CHECKS: List[Callable[[], object]] = [
    _check_binaries,
    _check_tts,
    _check_ollama,
    _check_image_models,
    _check_assets,
    _check_s3,
    _check_youtube,
    _check_public_url,
    _check_webhook,
]


def run_preflight() -> PreflightResponse:
    """모든 점검을 실행합니다. 개별 점검이 예외를 던져도 나머지는 계속 진행합니다."""
    results: List[PreflightCheck] = []
    for check in _CHECKS:
        try:
            outcome = check()
            results.extend(outcome if isinstance(outcome, list) else [outcome])
        except Exception as exc:
            results.append(_fail(check.__name__.lstrip("_"), WARNING, f"점검 중 오류: {exc}"))

    return PreflightResponse(
        ok=not any(c.severity == ERROR for c in results),
        checks=results,
    )


def log_preflight() -> None:
    """점검 결과를 로그로 요약합니다. 서버 기동 시 백그라운드 스레드에서 호출됩니다."""
    report = run_preflight()
    logger.info("===== 기동 점검 (preflight) =====")
    for check in report.checks:
        if check.ok:
            logger.info("  [OK]   %s: %s", check.name, check.detail)
        elif check.severity == ERROR:
            logger.error("  [오류] %s: %s", check.name, check.detail)
        else:
            logger.warning("  [주의] %s: %s", check.name, check.detail)
    logger.info("===== 점검 결과: %s =====", "정상" if report.ok else "오류 있음 — 위 항목 확인")


def _youtube_check_loop(interval_sec: float) -> None:
    while True:
        time.sleep(interval_sec)
        try:
            check = _check_youtube()
        except Exception as exc:  # 점검 스레드가 죽지 않도록
            logger.error("YouTube 토큰 주기 점검 중 오류: %s", exc)
            continue
        if check.ok:
            logger.info("YouTube 토큰 주기 점검: %s", check.detail)
        elif check.severity == ERROR:
            logger.error("YouTube 토큰 주기 점검: %s", check.detail)
        else:
            logger.warning("YouTube 토큰 주기 점검: %s", check.detail)


def start_youtube_token_monitor() -> None:
    """
    YouTube 토큰을 주기적으로 점검하는 백그라운드 스레드를 띄웁니다.

    토큰이 죽어도 업로드 요청이 오기 전까지는 아무도 모르기 때문에, 주기적으로 갱신을
    시도해 로그로 알립니다. 갱신 시도 자체가 refresh token 사용으로 잡혀
    "6개월 미사용 만료" 도 막아 줍니다.
    """
    hours = settings.YOUTUBE_TOKEN_CHECK_HOURS
    if hours <= 0 or not settings.YOUTUBE_TOKEN_FILE.exists():
        return
    threading.Thread(
        target=_youtube_check_loop, args=(hours * 3600,), name="youtube-token-monitor", daemon=True
    ).start()
