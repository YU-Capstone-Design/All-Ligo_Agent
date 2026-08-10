"""
전체 API 라우터 집합.

각 도메인 라우터를 하나로 모아 `app.main` 이 한 번만 include 하도록 합니다.
새 엔드포인트를 추가할 때는 `app/api/routes/` 에 모듈을 만들고 여기에 등록하세요.
"""

from fastapi import APIRouter

from app.api.routes import home, marketing, system, vision, weather

api_router = APIRouter()

# 등록 순서가 OpenAPI 문서(/docs)의 태그 노출 순서에 영향을 줍니다.
api_router.include_router(marketing.router)
api_router.include_router(weather.router)
api_router.include_router(system.router)
api_router.include_router(vision.router)
api_router.include_router(home.router)
