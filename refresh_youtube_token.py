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
                print("Please place the client secrets JSON in this directory.")
                return

            print("Performing authentication flow. A browser window should open...")
            flow = InstalledAppFlow.from_client_secrets_file('client_secret.json', SCOPES)
            creds = flow.run_local_server(port=8989)

        with open('token.json', 'w') as token_file:
            token_file.write(creds.to_json())
            print("Token successfully saved/updated in 'token.json'.")
    else:
        print("Token is still valid! No action needed.")

if __name__ == "__main__":
    main()
