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
