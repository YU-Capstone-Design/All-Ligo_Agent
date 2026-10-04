"""
홈(서버 접속 확인) 라우터.

HTML은 코드에 하드코딩하지 않고 `app/templates/home.html` 에서 읽어옵니다.
정적인 페이지 하나뿐이라 Jinja2 같은 템플릿 엔진 대신 파일을 그대로 읽어 캐시합니다.
"""

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.core.config import settings

router = APIRouter(tags=["🏠 홈"])

_TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates"
_TEMPLATE_PATH = _TEMPLATE_DIR / "home.html"


@lru_cache(maxsize=1)
def _load_home_html() -> str:
    """홈 페이지 HTML을 읽어 캐시합니다. 요청마다 디스크를 읽지 않도록 합니다."""
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


@router.get(
    "/",
    response_class=HTMLResponse,
    summary="서버 접속 확인 테스트 페이지",
    description="브라우저에서 이 URL에 접속하면 서버가 정상 실행 중인지 확인할 수 있는 HTML 페이지가 표시됩니다. API 기능과는 무관한 순수 테스트용 페이지입니다.",
)
async def home() -> HTMLResponse:
    return HTMLResponse(content=_load_home_html())


@lru_cache(maxsize=1)
def _load_privacy_html() -> str:
    """
    개인정보처리방침 HTML 을 읽어 캐시합니다.

    문의처 이메일은 공개 저장소에 박아 두지 않도록 설정값(AGENT_CONTACT_EMAIL)에서 넣습니다.
    """
    html = (_TEMPLATE_DIR / "privacy.html").read_text(encoding="utf-8")
    contact = (
        f'<a href="mailto:{settings.AGENT_CONTACT_EMAIL}">{settings.AGENT_CONTACT_EMAIL}</a>'
        if settings.AGENT_CONTACT_EMAIL
        else "Google 로그인 동의 화면에 표시된 개발자 지원 이메일로 문의해 주세요."
    )
    return html.replace("{{CONTACT}}", contact)


@router.get(
    "/privacy",
    response_class=HTMLResponse,
    summary="개인정보처리방침",
    description="Google OAuth 동의 화면(앱 게시)에 등록하는 개인정보처리방침 페이지입니다.",
)
async def privacy() -> HTMLResponse:
    return HTMLResponse(content=_load_privacy_html())
