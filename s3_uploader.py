import os
import boto3
from botocore.exceptions import NoCredentialsError, ClientError
from dotenv import load_dotenv

load_dotenv()

def upload_video_to_s3(file_path: str, object_name: str = None) -> str:
    """
    Upload a file to an S3 bucket and return the public URL.
    
    Expects the following environment variables (or AWS credentials configured):
    AWS_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY
    AWS_REGION (e.g., ap-northeast-2)
    AWS_S3_BUCKET
    AWS_S3_BASE_URL (optional)
    """
    bucket_name = os.getenv("AWS_S3_BUCKET")
    region_name = os.getenv("AWS_REGION", "ap-northeast-2")
    
    if not bucket_name:
        raise ValueError("[S3 Uploader] AWS_S3_BUCKET environment variable is not set.")
    
    if object_name is None:
        object_name = os.path.basename(file_path)

    s3_client = boto3.client('s3')

    try:
        print(f"[S3 Uploader] Uploading {file_path} to s3://{bucket_name}/{object_name}")
        # If the bucket is not public by default, you might want ExtraArgs={'ACL': 'public-read'}
        # But depending on the user's bucket settings, we'll just upload normally.
        # Ensure we set the correct content type for videos
        s3_client.upload_file(
            file_path, 
            bucket_name, 
            object_name,
            ExtraArgs={'ContentType': 'video/mp4'}
        )
        
        # Generate the S3 URL
        base_url = os.getenv("AWS_S3_BASE_URL")
        if base_url:
            s3_url = f"{base_url.rstrip('/')}/{object_name}"
        elif region_name == "us-east-1":
            s3_url = f"https://{bucket_name}.s3.amazonaws.com/{object_name}"
        else:
            s3_url = f"https://{bucket_name}.s3.{region_name}.amazonaws.com/{object_name}"
            
        print(f"[S3 Uploader] Upload successful. URL: {s3_url}")
        return s3_url

    except FileNotFoundError:
        print(f"[S3 Uploader] The file {file_path} was not found")
        raise
    except NoCredentialsError:
        print("[S3 Uploader] AWS credentials not available")
        raise
    except ClientError as e:
        print(f"[S3 Uploader] AWS ClientError: {e}")
        raise
