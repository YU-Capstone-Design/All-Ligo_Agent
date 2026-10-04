"""
YouTube OAuth 토큰 발급 스크립트 (수동 URL 붙여넣기 방식).

브라우저를 띄울 수 없는 원격 서버(SSH 접속 등)에서 사용합니다.
인증 URL을 직접 브라우저에 붙여넣고, 리다이렉트된 주소를 다시 터미널에 붙여넣는 방식입니다.

브라우저를 열 수 있는 로컬 환경이라면 `refresh_youtube_token.py` 가 더 편합니다.

실행:
    python scripts/refresh_youtube_token_manual.py
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# 프로젝트 루트를 import 경로에 추가해 app 패키지를 불러올 수 있게 합니다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from app.core.config import settings

SCOPES = list(settings.YOUTUBE_SCOPES)
TOKEN_FILE = settings.YOUTUBE_TOKEN_FILE
CLIENT_SECRET_FILE = settings.YOUTUBE_CLIENT_SECRET_FILE
META_FILE = settings.YOUTUBE_TOKEN_META_FILE

# 실제로 서버를 띄우지는 않고, 리다이렉트 주소만 이 값으로 고정합니다.
_REDIRECT_URI = "http://localhost:8989"
# 붙여넣기 재시도 횟수
_MAX_PASTE_ATTEMPTS = 3


def _extract_code(pasted: str, expected_state: str) -> tuple[str | None, str | None]:
    """
    붙여넣은 리다이렉트 주소에서 인증 코드를 꺼냅니다.

    Returns:
        (code, None) 또는 (None, 오류 설명).
        주소 대신 코드만 붙여넣어도 받아 줍니다.
    """
    if not pasted:
        return None, "입력이 비어 있습니다."
    if not pasted.startswith("http"):
        return pasted, None  # 코드만 붙여넣은 경우

    params = parse_qs(urlparse(pasted).query)
    if "error" in params:
        return None, f"구글이 오류를 돌려줬습니다: {params['error'][0]}"
    state = params.get("state", [None])[0]
    if state and state != expected_state:
        return None, "state 값이 이번 요청과 다릅니다(다른 로그인 시도의 주소)."
    code = params.get("code", [None])[0]
    if not code:
        return None, "주소에서 code 를 찾지 못했습니다."
    return code, None


def _run_manual_flow() -> Credentials | None:
    """인증 URL을 출력하고, 사용자가 붙여넣은 리다이렉트 주소로 토큰을 교환합니다."""
    flow = InstalledAppFlow.from_client_secrets_file(
        str(CLIENT_SECRET_FILE), SCOPES, redirect_uri=_REDIRECT_URI
    )

    # 1) 사용자가 브라우저에서 열어야 할 인증 URL 출력
    auth_url, expected_state = flow.authorization_url(prompt="consent")
    print("\n" + "=" * 60)
    print("1. 아래 URL을 복사하여 브라우저(시크릿 창 추천)에 붙여넣으세요:")
    print(auth_url)
    print("=" * 60 + "\n")

    # 2) 리다이렉트된 주소창 URL 전체를 입력받음
    #    (localhost:8989 에는 아무것도 없으므로 브라우저는 오류 화면을 보여줍니다. 정상입니다.)
    #    이 프로세스가 PKCE 검증 값을 들고 있으므로, 실패해도 종료하지 않고 다시 입력받습니다.
    #    (종료하면 방금 받은 인증 코드는 다시 쓸 수 없어 로그인부터 다시 해야 합니다)
    print("2. 구글 로그인 후 무한 로딩(또는 오류 화면)이 뜨면,")
    print("   브라우저 주소창(URL)을 '통째로 복사'해서 아래에 붙여넣고 엔터를 치세요.")
    token_response = None
    for attempt in range(1, _MAX_PASTE_ATTEMPTS + 1):
        redirect_response = input("주소창 URL 입력: ").strip()
        code, error = _extract_code(redirect_response, expected_state)
        if error:
            print(f"   → {error} 다시 붙여넣어 주세요. ({attempt}/{_MAX_PASTE_ATTEMPTS})")
            continue

        # 3) code 로 토큰 교환.
        #    authorization_response=URL 로 넘기면 oauthlib 이 http://localhost 주소를 보고
        #    "https 만 허용" 오류를 내므로, code 만 직접 넘깁니다. (토큰 교환 자체는 https)
        try:
            token_response = flow.fetch_token(code=code)
            break
        except Exception as exc:
            print(f"   → 토큰 교환 실패: {exc} ({attempt}/{_MAX_PASTE_ATTEMPTS})")

    if token_response is None:
        print("토큰을 받지 못했습니다. 스크립트를 다시 실행해 처음부터 진행하세요.")
        return None

    _save_token_meta(token_response)
    return flow.credentials


def _save_token_meta(token_response: dict) -> None:
    """
    발급 시각과 refresh token 만료 시각을 기록합니다. (토큰 값은 기록하지 않음)

    Google 은 OAuth 동의 화면이 "테스트" 상태면 7일짜리 refresh token 을 주고,
    이때 응답에 남은 수명(refresh_token_expires_in)을 함께 보냅니다.
    서버의 기동 점검이 이 기록을 보고 만료 임박을 경고합니다.
    """
    now = datetime.now()
    expires_in = token_response.get("refresh_token_expires_in")
    expires_at = (now + timedelta(seconds=int(expires_in))).isoformat(timespec="seconds") if expires_in else None

    META_FILE.write_text(json.dumps(
        {"issuedAt": now.isoformat(timespec="seconds"), "refreshTokenExpiresAt": expires_at}, indent=2
    ))
    if expires_at:
        print(f"\n⚠️  이 refresh token 은 {expires_at} 에 만료됩니다 (약 {int(expires_in) // 86400}일).")
        print("   OAuth 동의 화면이 '테스트' 상태로 보입니다. '프로덕션'으로 게시한 뒤 다시 발급하세요.")
    else:
        print("\n✅ 만료 기한이 없는 refresh token 입니다 (동의 화면 '프로덕션' 상태).")


def main() -> None:
    print("=== YouTube OAuth 토큰 갱신 도구 (수동 모드) ===")

    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
            print(f"기존 토큰 파일을 찾았습니다: {TOKEN_FILE}")
        except Exception as exc:
            print(f"기존 토큰 로드 실패: {exc}")
            creds = None

    if creds and creds.valid:
        print("토큰이 아직 유효합니다. 할 일이 없습니다.")
        return

    # 만료되었지만 refresh_token 이 있으면 먼저 조용히 갱신을 시도합니다.
    if creds and creds.expired and creds.refresh_token:
        print("토큰이 만료되었습니다. 갱신을 시도합니다...")
        try:
            creds.refresh(Request())
            print("토큰 갱신 성공!")
        except Exception as exc:
            print(f"토큰 갱신 실패: {exc}. 수동 인증으로 전환합니다.")
            creds = None

    if not creds:
        if not CLIENT_SECRET_FILE.exists():
            print(f"오류: '{CLIENT_SECRET_FILE}' 파일이 없습니다.")
            return

        creds = _run_manual_flow()
        if not creds:
            return

    TOKEN_FILE.write_text(creds.to_json())
    os.chmod(TOKEN_FILE, 0o600)  # 토큰 파일은 소유자만 읽도록
    print(f"\n🎉 토큰을 저장했습니다: {TOKEN_FILE}")


if __name__ == "__main__":
    main()
