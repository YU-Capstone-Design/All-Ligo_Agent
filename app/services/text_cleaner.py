"""
LLM 출력 후처리 모듈.

로컬 LLM은 지시를 어기고 "마케팅 문구:" 같은 접두사나 해시태그를 덧붙이는 경우가
잦습니다. 생성된 텍스트를 그대로 자막/게시물로 쓸 수 있도록 정리합니다.
"""

import re
from typing import List

# LLM 응답에서 이미지 프롬프트 줄을 뽑아내는 패턴.
# "[IMAGE_PROMPT]: ..." 와 "[IMAGE_PROMPT_1]: ..." 두 형태를 모두 잡습니다.
_IMAGE_PROMPT_PATTERN = re.compile(r"\[IMAGE_PROMPT(?:_\d+)?\]:\s*(.*)")

# 홍보 텍스트 뒤에 붙은 이미지 프롬프트 블록 전체를 잘라내는 패턴
_IMAGE_PROMPT_BLOCK = re.compile(r"\[IMAGE_PROMPT.*", re.DOTALL)

# 응답 맨 앞에 붙는 메타 접두사 ("마케팅 문구:", "내용 -" 등)
_LEADING_META = re.compile(
    r"^(?:홍보\s*텍스트|마케팅\s*문구|홍보\s*문구|텍스트\s*내용|내용|자막|출력\s*형식|문구)[\s\:\-]*",
    re.IGNORECASE,
)

# 응답 맨 끝에 남은 빈 라벨
_TRAILING_META = re.compile(r"(?:마케팅\s*문구\s*:?)$", re.IGNORECASE)

# 해시태그. 나레이션용 문장에는 '#'가 들어가면 안 됩니다.
_HASHTAG = re.compile(r"#\S+")

# 이모지와 그 부속 문자(변형 선택자, ZWJ, 피부색 등).
# 자막 폰트(Pretendard/Noto CJK)에는 이모지 글리프가 없어 영상에 네모(□)로 찍힙니다.
_EMOJI = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # 이모티콘, 그림 문자, 교통/지도, 보충 기호 등
    "☀-➿"          # 기타 기호(☀☕), 딩뱃(✨✅)
    "⬀-⯿"          # 별·화살표 기호(⭐)
    "⌀-⏿"          # 기술 기호(⏰⌛)
    "︀-️"          # 변형 선택자
    "‍"                 # ZWJ (이모지 결합용)
    "⃣"                 # 키캡 결합 문자
    "]+"
)


def extract_image_prompts(raw_text: str, limit: int = 3) -> List[str]:
    """
    LLM 응답에서 이미지 생성용 영어 프롬프트를 추출합니다.

    Args:
        raw_text: LLM의 원본 응답.
        limit: 최대 추출 개수.

    Returns:
        앞뒤 공백이 제거된 프롬프트 문자열 목록 (최대 limit개).
    """
    prompts = _IMAGE_PROMPT_PATTERN.findall(raw_text)
    return [p.strip() for p in prompts[:limit]]


def clean_marketing_text(raw_text: str) -> str:
    """
    LLM 응답에서 실제 게시에 쓸 홍보 텍스트만 남깁니다.

    처리 순서
      1. 뒤에 붙은 [IMAGE_PROMPT] 블록 제거
      2. 맨 앞 메타 접두사 제거
      3. 맨 뒤 빈 라벨 제거
      4. 해시태그(#...) 제거
      5. 감싸고 있는 따옴표/공백 정리
    """
    text = _IMAGE_PROMPT_BLOCK.sub("", raw_text).strip()
    text = _LEADING_META.sub("", text).strip()
    text = _TRAILING_META.sub("", text).strip()
    text = _HASHTAG.sub("", text).strip()
    return text.strip("'\" \n")


def strip_emoji(text: str) -> str:
    """
    영상 자막·나레이션용으로 이모지를 제거합니다.

    웹훅으로 나가는 generatedText 에는 이모지를 남겨둡니다(게시글/설명란 용도).
    영상에 그려지는 자막만 폰트가 이모지를 지원하지 않아 네모로 깨지기 때문에 제거합니다.
    """
    text = _EMOJI.sub("", text)
    # 이모지가 빠진 자리에 생긴 연속 공백을 하나로 줄입니다(줄바꿈은 유지).
    text = re.sub(r"[ \t]{2,}", " ", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()
