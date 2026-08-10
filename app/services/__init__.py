"""
비즈니스 로직 계층.

라우터는 요청 검증과 응답 변환만 담당하고, 실제 처리(AI 호출, 파일 생성,
외부 API 연동)는 모두 이 계층에 둡니다.

    content_pipeline  - 콘텐츠 생성 백그라운드 파이프라인 (전체 흐름 조립)
    weather_service   - Open-Meteo 날씨 조회
    system_service    - GPU/디스크 상태 및 가용 여부 판정
    vision_service    - LLaVA 이미지 분석
    image_service     - FLUX/SDXL 포스터 이미지 생성
    video/            - FFmpeg 숏폼 영상 생성 패키지
    storage_service   - AWS S3 업로드
    youtube_service   - YouTube 업로드
    image_fetcher     - 요청 이미지 URL 병렬 다운로드
    text_cleaner      - LLM 출력 후처리
    webhook_service   - Spring 백엔드 결과 통보
"""
