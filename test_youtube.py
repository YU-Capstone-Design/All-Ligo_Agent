import os
import sys
import youtube_uploader

video_path = "static/videos/shortform_1779260530.mp4"
title = "All-Ligo Test Video #shorts"
description = "This is a test video uploaded automatically by All-Ligo Marketing AI Agent."
tags = ["test", "shortform", "allligo"]

if __name__ == "__main__":
    if not os.path.exists(video_path):
        print(f"Error: Video file not found at {video_path}")
        sys.exit(1)
        
    print(f"Starting test upload for {video_path}...")
    try:
        url = youtube_uploader.upload_video(video_path, title, description, tags)
        print("\n" + "="*50)
        print("Success! YouTube URL:")
        print(url)
        print("="*50)
    except Exception as e:
        print("\n" + "="*50)
        print(f"Upload failed with error: {e}")
        print("="*50)
