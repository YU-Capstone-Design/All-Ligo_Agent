import os
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

def main():
    print("=== YouTube OAuth Token Refresher ===")
    creds = None
    if os.path.exists('token.json'):
        try:
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
            print("Found existing token.json.")
        except Exception as e:
            print(f"Error loading existing token: {e}")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Token expired. Attempting refresh...")
            try:
                creds.refresh(Request())
                print("Token successfully refreshed!")
            except Exception as e:
                print(f"Token refresh failed: {e}. Re-authenticating...")
                creds = None

        if not creds:
            if not os.path.exists('client_secret.json'):
                print("ERROR: 'client_secret.json' file is missing.")
                return

            # OOB(Out-Of-Band) 수동 입력을 위한 리다이렉트 URI 설정
            # 로컬 서버를 띄우지 않고 8989 포트로 리다이렉트 주소만 강제 지정합니다.
            flow = InstalledAppFlow.from_client_secrets_file(
                'client_secret.json', 
                SCOPES, 
                redirect_uri='http://localhost:8989'
            )
            
            # 1. 인증 URL 생성 및 출력
            auth_url, _ = flow.authorization_url(prompt='consent')
            print("\n" + "="*60)
            print("1. 아래 URL을 복사하여 브라우저(시크릿 창 추천)에 붙여넣으세요:")
            print(auth_url)
            print("="*60 + "\n")
            
            # 2. 브라우저에서 리다이렉트된 주소를 통째로 입력받음
            print("2. 구글 로그인 후 무한 로딩(또는 오류 화면)이 뜨면,")
            print("   브라우저 주소창(URL)을 '통째로 복사'해서 아래에 붙여넣고 엔터를 치세요.")
            redirect_response = input("주소창 URL 입력: ").strip()
            
            # 3. 주소창에서 코드를 파싱하여 토큰 획득
            try:
                flow.fetch_token(authorization_response=redirect_response)
                creds = flow.credentials
            except Exception as e:
                print(f"인증 처리 중 오류 발생: {e}")
                return

        with open('token.json', 'w') as token_file:
            token_file.write(creds.to_json())
            print("\n🎉 Token successfully saved/updated in 'token.json'!")
    else:
        print("Token is still valid! No action needed.")

if __name__ == "__main__":
    main()