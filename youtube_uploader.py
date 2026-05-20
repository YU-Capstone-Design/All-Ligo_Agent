import os
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# YouTube Video Upload Scope
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

def get_authenticated_service():
    """
    Handles OAuth 2.0 authentication.
    Reads 'client_secret.json', runs InstalledAppFlow for first-time login,
    and caches/refreshes tokens in 'token.json'.
    """
    creds = None
    
    # Check if we have a cached token
    if os.path.exists('token.json'):
        try:
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        except Exception as e:
            print(f"[YouTube Uploader] Error loading cached token: {e}")
            creds = None

    # If there are no valid credentials, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[YouTube Uploader] Token expired. Attempting refresh...")
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"[YouTube Uploader] Token refresh failed: {e}. Re-authenticating...")
                creds = None
        
        # If refreshing failed or wasn't possible, run full flow
        if not creds:
            if not os.path.exists('client_secret.json'):
                raise FileNotFoundError(
                    "[YouTube Uploader] 'client_secret.json' file is missing. "
                    "Please place the client secrets JSON in the All-Ligo_Agent root directory."
                )
            
            print("[YouTube Uploader] Performing first-time authentication...")
            flow = InstalledAppFlow.from_client_secrets_file('client_secret.json', SCOPES)
            # Run local server to authenticate. It will block until authorization is complete.
            creds = flow.run_local_server(port=8989)
            
        # Save credentials for the next run
        with open('token.json', 'w') as token_file:
            token_file.write(creds.to_json())
            print("[YouTube Uploader] Token successfully saved/updated in 'token.json'")

    return build('youtube', 'v3', credentials=creds)

def upload_video(file_path: str, title: str, description: str, tags: list = None) -> str:
    """
    Uploads a video to YouTube with privacy status set to 'unlisted'.
    Uses MediaFileUpload for resumable chunked uploading.
    Returns the watch URL: https://youtube.com/shorts/{videoId}
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"[YouTube Uploader] Video file not found: {file_path}")

    # Build authenticated YouTube service
    youtube = get_authenticated_service()

    body = {
        'snippet': {
            'title': title[:100],  # Title is capped at 100 characters on YouTube
            'description': description,
            'tags': tags or [],
            'categoryId': '22'  # 'People & Blogs' category
        },
        'status': {
            'privacyStatus': 'unlisted',
            'selfDeclaredMadeForKids': False
        }
    }

    # Use 5MB chunks for resumable upload
    media = MediaFileUpload(
        file_path,
        mimetype='video/mp4',
        chunksize=1024*1024*5,
        resumable=True
    )

    request = youtube.videos().insert(
        part='snippet,status',
        body=body,
        media_body=media
    )

    print(f"[YouTube Uploader] Starting upload for {file_path}...")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"[YouTube Uploader] Upload progress: {int(status.progress() * 100)}%")

    video_id = response.get('id')
    if not video_id:
        raise Exception("[YouTube Uploader] Video upload completed, but no video ID was returned by YouTube.")

    print(f"[YouTube Uploader] Upload succeeded. Video ID: {video_id}")
    return f"https://youtube.com/shorts/{video_id}"
