import base64
import requests
import json

# 1x1 black pixel base64
img_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="

url = "http://localhost:11434/api/chat"
payload = {
    "model": "llava:latest",
    "messages": [
        {
            "role": "user",
            "content": "What is in this image?",
            "images": [img_b64]
        }
    ],
    "stream": False
}

print("Sending request...")
response = requests.post(url, json=payload, timeout=30)
print(response.status_code)
print(response.json())
