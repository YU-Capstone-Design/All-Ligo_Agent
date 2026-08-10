"""
YouTube OAuth 토큰 발급 스크립트 (수동 URL 붙여넣기 방식).

브라우저를 띄울 수 없는 원격 서버(SSH 접속 등)에서 사용합니다.
인증 URL을 직접 브라우저에 붙여넣고, 리다이렉트된 주소를 다시 터미널에 붙여넣는 방식입니다.

브라우저를 열 수 있는 로컬 환경이라면 `refresh_youtube_token.py` 가 더 편합니다.

실행:
    python scripts/refresh_youtube_token_manual.py
"""

import sys
from pathlib import Path

# 프로젝트 루트를 import 경로에 추가해 app 패키지를 불러올 수 있게 합니다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from app.core.config import settings

SCOPES = list(settings.YOUTUBE_SCOPES)
TOKEN_FILE = settings.YOUTUBE_TOKEN_FILE
CLIENT_SECRET_FILE = settings.YOUTUBE_CLIENT_SECRET_FILE

# 실제로 서버를 띄우지는 않고, 리다이렉트 주소만 이 값으로 고정합니다.
_REDIRECT_URI = "http://localhost:8989"


def _run_manual_flow() -> Credentials | None:
    """인증 URL을 출력하고, 사용자가 붙여넣은 리다이렉트 주소로 토큰을 교환합니다."""
    flow = InstalledAppFlow.from_client_secrets_file(
        str(CLIENT_SECRET_FILE), SCOPES, redirect_uri=_REDIRECT_URI
    )

    # 1) 사용자가 브라우저에서 열어야 할 인증 URL 출력
    auth_url, _ = flow.authorization_url(prompt="consent")
    print("\n" + "=" * 60)
    print("1. 아래 URL을 복사하여 브라우저(시크릿 창 추천)에 붙여넣으세요:")
    print(auth_url)
    print("=" * 60 + "\n")

    # 2) 리다이렉트된 주소창 URL 전체를 입력받음
    #    (localhost:8989 에는 아무것도 없으므로 브라우저는 오류 화면을 보여줍니다. 정상입니다.)
    print("2. 구글 로그인 후 무한 로딩(또는 오류 화면)이 뜨면,")
    print("   브라우저 주소창(URL)을 '통째로 복사'해서 아래에 붙여넣고 엔터를 치세요.")
    redirect_response = input("주소창 URL 입력: ").strip()

    # 3) URL의 code 파라미터로 토큰 교환
    try:
        flow.fetch_token(authorization_response=redirect_response)
        return flow.credentials
    except Exception as exc:
        print(f"인증 처리 중 오류 발생: {exc}")
        return None


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
    print(f"\n🎉 토큰을 저장했습니다: {TOKEN_FILE}")


if __name__ == "__main__":
    main()
