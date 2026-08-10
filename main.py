"""
하위 호환용 진입점.

실제 애플리케이션은 `app/main.py` 에 있습니다. 이 파일은 기존 배포 스크립트나
`uvicorn main:app` 명령이 그대로 동작하도록 앱 객체만 재노출합니다.

새로 작성하는 스크립트에서는 아래 형태를 사용하세요.
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from app.main import app

__all__ = ["app"]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
