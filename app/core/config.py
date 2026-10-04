"""
애플리케이션 전역 설정 모듈.

환경 변수(.env 포함)를 이 파일 한 곳에서만 읽어 `settings` 싱글턴으로 노출합니다.
다른 모듈에서 os.environ / os.getenv 를 직접 호출하지 마세요. 설정이 흩어지면
"이 값이 어디서 오는지" 추적하기 어려워집니다.

경로 상수는 모두 프로젝트 루트(BASE_DIR) 기준의 **절대 경로**로 계산합니다.
기존 코드는 "static/images" 같은 상대 경로를 사용해서 서버를 실행한
작업 디렉터리(CWD)가 프로젝트 루트가 아니면 파일을 못 찾는 문제가 있었습니다.
"""

from pathlib import Path

from dotenv import load_dotenv
import os

# .env 파일을 프로세스 시작 시 1회만 로드합니다.
load_dotenv()


def _env_str(key: str, default: str) -> str:
    """환경 변수를 문자열로 읽습니다. 값이 비어 있으면 기본값을 사용합니다."""
    value = os.getenv(key)
    return value if value else default


def _env_int(key: str, default: int) -> int:
    """환경 변수를 정수로 읽습니다. 숫자로 변환할 수 없으면 기본값을 사용합니다."""
    try:
        return int(os.environ[key])
    except (KeyError, ValueError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    """환경 변수를 불리언으로 읽습니다. true/1/yes/on 만 참으로 봅니다."""
    value = os.getenv(key)
    if not value:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    """
    서버 실행에 필요한 모든 설정값의 단일 소스(Single Source of Truth).

    인스턴스는 모듈 하단의 `settings` 하나만 사용합니다.
    """

    # ------------------------------------------------------------------
    # 경로 설정
    # ------------------------------------------------------------------
    # 이 파일 위치: <루트>/app/core/config.py → parents[2] 가 프로젝트 루트
    BASE_DIR: Path = Path(__file__).resolve().parents[2]

    STATIC_DIR: Path = BASE_DIR / "static"
    IMAGES_DIR: Path = STATIC_DIR / "images"      # 생성/복사된 포스터 이미지
    VIDEOS_DIR: Path = STATIC_DIR / "videos"      # 렌더링된 숏폼 영상
    UPLOADS_DIR: Path = STATIC_DIR / "uploads"    # 요청으로 받은 원본 이미지(작업 후 삭제)
    BGM_DIR: Path = STATIC_DIR / "bgm"            # 영상 배경음악(mp3) 후보
    FONT_DIR: Path = BASE_DIR / "fonts"           # 자막 렌더링용 폰트

    # 서버 기동 시 미리 만들어 둘 디렉터리 목록
    RUNTIME_DIRS: tuple = (STATIC_DIR, IMAGES_DIR, VIDEOS_DIR, UPLOADS_DIR)

    # ------------------------------------------------------------------
    # 외부 연동
    # ------------------------------------------------------------------
    # 작업 완료 시 결과를 POST 할 Spring 백엔드 콜백 URL
    SPRING_WEBHOOK_URL: str = _env_str(
        "SPRING_WEBHOOK_URL",
        "http://localhost:8080/api/internal/content-callback",
    )
    WEBHOOK_TIMEOUT_SEC: int = _env_int("WEBHOOK_TIMEOUT_SEC", 5)

    # Open-Meteo 실시간 날씨 API
    WEATHER_API_URL: str = "https://api.open-meteo.com/v1/forecast"
    WEATHER_TIMEOUT_SEC: int = _env_int("WEATHER_TIMEOUT_SEC", 5)

    # ------------------------------------------------------------------
    # 로컬 LLM (Ollama)
    # ------------------------------------------------------------------
    OLLAMA_BASE_URL: str = _env_str("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_TEXT_MODEL: str = _env_str("OLLAMA_TEXT_MODEL", "gemma4:latest")
    OLLAMA_VISION_MODEL: str = _env_str("OLLAMA_VISION_MODEL", "llava:13b")
    OLLAMA_TEXT_TEMPERATURE: float = 0.7
    # 이미지 분석 타임아웃. keep_alive=0 이라 매번 모델을 새로 올리는데, llava:13b 는
    # 디스크에서 처음 올릴 때 로드만 30초 넘게 걸립니다(실측 총 39초, 캐시된 뒤에는 2초).
    # 15초로 두면 첫 분석이 항상 실패하므로 여유 있게 잡습니다.
    OLLAMA_VISION_TIMEOUT_SEC: int = _env_int("OLLAMA_VISION_TIMEOUT_SEC", 90)
    # 텍스트 생성 타임아웃. 지정하지 않으면 Ollama가 멈췄을 때 작업이 영원히 끝나지 않습니다.
    OLLAMA_TEXT_TIMEOUT_SEC: int = _env_int("OLLAMA_TEXT_TIMEOUT_SEC", 300)
    # gemma4는 thinking(사고) 모드를 지원하며, 끄지 않으면 기본으로 켜집니다.
    # 켜두면 응답이 약 2.5배 느려지고(실측 4초 → 10초) 가끔 사고가 길어져 수 분씩 걸립니다.
    OLLAMA_TEXT_THINKING: bool = _env_bool("OLLAMA_TEXT_THINKING", False)
    # 생성 토큰 상한. 실측 POST 약 300, VIDEO 약 180 토큰이므로 넉넉히 잡은 폭주 방지용 값입니다.
    OLLAMA_TEXT_MAX_TOKENS: int = _env_int("OLLAMA_TEXT_MAX_TOKENS", 2048)

    # ------------------------------------------------------------------
    # 이미지 생성 모델 (FLUX / SDXL)
    # ------------------------------------------------------------------
    # false(기본)면 로컬 Hugging Face 캐시에 있는 모델만 씁니다. 캐시에 없는 모델을
    # 요청 처리 중에 내려받기 시작하면(FLUX 는 수십 GB) 작업이 한없이 늘어지기 때문입니다.
    # 모델을 새로 받아야 할 때만 true 로 켜세요.
    IMAGE_MODEL_ALLOW_DOWNLOAD: bool = _env_bool("IMAGE_MODEL_ALLOW_DOWNLOAD", False)

    # ------------------------------------------------------------------
    # 동시 작업 / 리소스 임계값
    # ------------------------------------------------------------------
    MAX_CONCURRENT_JOBS: int = _env_int("MAX_CONCURRENT_JOBS", 2)
    MAX_IMAGE_URLS: int = 5                # 요청 1건당 허용하는 최대 이미지 URL 개수
    MIN_FREE_DISK_MB: int = 1000           # 이 값 이하면 busy 로 판정
    GPU_BUSY_UTIL_PERCENT: int = 90        # GPU 연산 사용률 임계값
    GPU_BUSY_MEM_PERCENT: int = 80         # GPU 메모리 사용률 임계값

    # ------------------------------------------------------------------
    # AWS S3
    # ------------------------------------------------------------------
    AWS_S3_BUCKET: str = _env_str("AWS_S3_BUCKET", "")
    AWS_REGION: str = _env_str("AWS_REGION", "ap-northeast-2")
    AWS_S3_BASE_URL: str = _env_str("AWS_S3_BASE_URL", "")

    # ------------------------------------------------------------------
    # YouTube OAuth
    # ------------------------------------------------------------------
    YOUTUBE_SCOPES: tuple = ("https://www.googleapis.com/auth/youtube.upload",)
    YOUTUBE_CLIENT_SECRET_FILE: Path = BASE_DIR / "client_secret.json"
    YOUTUBE_TOKEN_FILE: Path = BASE_DIR / "token.json"

    # ------------------------------------------------------------------
    # 헬퍼
    # ------------------------------------------------------------------
    def to_relative(self, path: Path | str) -> str:
        """
        절대 경로를 프로젝트 루트 기준 상대 경로 문자열로 변환합니다.

        웹훅 payload 의 `localVideoPath` 는 예전부터 "static/videos/xxx.mp4" 형태의
        상대 경로였고, Spring 이 그 값을 그대로 업로드 API 에 되돌려줍니다.
        내부적으로는 절대 경로를 쓰되 외부로 나가는 값은 기존 형식을 유지합니다.
        """
        path = Path(path)
        try:
            return str(path.resolve().relative_to(self.BASE_DIR))
        except ValueError:
            # 프로젝트 루트 밖의 경로라면 변환하지 않고 그대로 반환합니다.
            return str(path)

    def resolve_path(self, path: str) -> Path:
        """
        외부에서 받은 경로 문자열을 절대 경로로 해석합니다.

        `to_relative()` 의 역연산입니다. 상대 경로면 프로젝트 루트를 기준으로
        붙이고, 이미 절대 경로면 그대로 사용합니다.
        """
        candidate = Path(path)
        return candidate if candidate.is_absolute() else self.BASE_DIR / candidate


# 애플리케이션 전역에서 공유하는 설정 인스턴스
settings = Settings()
