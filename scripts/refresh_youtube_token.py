"""
YouTube OAuth 토큰 발급/갱신 스크립트 (자동 브라우저 방식).

서버는 대화형 로그인을 할 수 없으므로, token.json 은 이 스크립트로 미리 만들어 둡니다.
로컬에서 브라우저를 띄울 수 있는 환경에서 실행하세요.
브라우저를 열 수 없는 원격 서버라면 `refresh_youtube_token_manual.py` 를 쓰세요.

실행:
    python scripts/refresh_youtube_token.py
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

# OAuth 리다이렉트를 받을 로컬 포트
_LOCAL_SERVER_PORT = 8989


def load_existing_credentials():
    """저장된 token.json 을 읽습니다. 없거나 손상되었으면 None을 반환합니다."""
    if not TOKEN_FILE.exists():
        return None

    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        print(f"기존 토큰 파일을 찾았습니다: {TOKEN_FILE}")
        return creds
    except Exception as exc:
        print(f"기존 토큰 로드 실패: {exc}")
        return None


def main() -> None:
    print("=== YouTube OAuth 토큰 갱신 도구 ===")
    creds = load_existing_credentials()

    if creds and creds.valid:
        print("토큰이 아직 유효합니다. 할 일이 없습니다.")
        return

    # 1) 만료되었지만 refresh_token 이 있으면 조용히 갱신을 시도합니다.
    if creds and creds.expired and creds.refresh_token:
        print("토큰이 만료되었습니다. 갱신을 시도합니다...")
        try:
            creds.refresh(Request())
            print("토큰 갱신 성공!")
        except Exception as exc:
            print(f"토큰 갱신 실패: {exc}. 처음부터 다시 인증합니다.")
            creds = None

    # 2) 갱신이 불가능하면 브라우저를 열어 전체 인증 플로우를 진행합니다.
    if not creds:
        if not CLIENT_SECRET_FILE.exists():
            print(f"오류: '{CLIENT_SECRET_FILE}' 파일이 없습니다.")
            print("Google Cloud Console에서 받은 클라이언트 시크릿 JSON을 프로젝트 루트에 두세요.")
            return

        print("인증 플로우를 시작합니다. 브라우저 창이 열립니다...")
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
        creds = flow.run_local_server(port=_LOCAL_SERVER_PORT)

    TOKEN_FILE.write_text(creds.to_json())
    print(f"토큰을 저장했습니다: {TOKEN_FILE}")


if __name__ == "__main__":
    main()
