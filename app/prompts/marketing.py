"""
마케팅 텍스트 생성용 LLM 프롬프트 조립 모듈.

기존에는 워커 함수 안에 200줄짜리 f-string이 조건 분기와 뒤엉켜 있었습니다.
프롬프트는 결과 품질을 좌우하는 핵심 자산이므로 별도 모듈로 분리해서,
로직 수정과 문구 튜닝이 서로 간섭하지 않게 했습니다.

주의: 반환되는 문자열은 LangChain `PromptTemplate` 에 그대로 들어갑니다.
따라서 `{weather_data}` 처럼 **단일 중괄호**로 남은 자리는 템플릿 변수로 취급됩니다.
문구를 수정할 때 중괄호를 실수로 추가하면 KeyError가 발생하니 주의하세요.
"""

from typing import List

# PromptTemplate에 전달해야 하는 변수 이름 목록
TEMPLATE_VARIABLES = ["weather_data", "mood_tag", "hash_tag", "user_prompt", "upload_day", "upload_time"]

# 이미지 프롬프트를 함께 뽑아야 할 때(TRANSFORM + VIDEO) 붙이는 지시문.
# '{mood_tag}' 는 의도적으로 단일 중괄호입니다 → PromptTemplate이 채웁니다.
_IMAGE_PROMPT_INSTRUCTION = (
    "맨 마지막 줄에 포스터 이미지를 만들기 위한 [IMAGE_PROMPT]: (영어 프롬프트) 를 작성해주세요.\n\n"
    "날씨에 어울리는 시각적 분위기(visual cue)와 분위기 태그({mood_tag})의 감성을 반영한 "
    "3개의 서로 다른 고품질 이미지 프롬프트를 반드시 영어로 작성하세요.\n"
    "중요: 이미지 프롬프트를 작성할 때, '사용자 추가 요청'에 명시된 특징(동물, 사물, 형태 등)이 있다면 "
    "반드시 이를 메인 피사체로 삼아 작성하세요. 만약 사용자 요청에서 명확한 형체를 확인할 수 없다면 "
    "'업로드 이미지 분석 결과'에 나온 피사체를 우선적으로 사용하세요."
)

_NO_IMAGE_PROMPT_INSTRUCTION = "이미지 생성은 하지 않으므로 [IMAGE_PROMPT]는 절대 작성하지 마세요."

# 이미지 프롬프트 3개를 받아내기 위한 출력 형식 예시
_IMAGE_PROMPT_OUTPUT_FORMAT = """
[IMAGE_PROMPT_1]: (English description for image 1)
[IMAGE_PROMPT_2]: (English description for image 2)
[IMAGE_PROMPT_3]: (English description for image 3)
"""

# contentType 별 분량/톤 지시
_CONTENT_TYPE_INSTRUCTIONS = {
    "POST": "블로그나 인스타그램 포스트용이므로, 이모지를 포함하여 3문단 이상의 충분한 길이로 상세한 홍보 글을 작성하세요.",
    "VIDEO": "숏폼 영상의 자막 및 설명란 용도이므로, 띄어쓰기 포함 50자 이내, 짧고 강렬한 1~2문장으로 작성하세요.",
}


def needs_image_prompts(content_type: str, resolved_mode: str) -> bool:
    """
    LLM에게 이미지 생성용 영어 프롬프트까지 받아야 하는 조합인지 판정합니다.

    영상(VIDEO)을 AI 이미지로 새로 만드는 TRANSFORM 모드일 때만 필요합니다.
    ORIGINAL 모드는 사용자가 올린 원본 이미지를 그대로 쓰므로 불필요합니다.
    """
    return resolved_mode == "TRANSFORM" and content_type == "VIDEO"


def build_vision_section(analysis_result: dict) -> str:
    """업로드 이미지 분석(LLaVA) 결과를 프롬프트에 삽입할 블록으로 변환합니다."""
    objects = ", ".join(analysis_result.get("objects", []))
    mood = ", ".join(analysis_result.get("mood", []))
    colors = ", ".join(analysis_result.get("colors", []))

    return (
        f"\n[업로드 이미지 분석 결과]\n"
        f"- 주요 객체: {objects}\n"
        f"- 분위기: {mood}\n"
        f"- 주요 색상: {colors}\n"
        f"이 분석 결과를 바탕으로 새로운 마케팅 텍스트를 작성하세요."
    )


def build_top_performers_section(top_performers_context: str) -> str:
    """
    과거 성과가 좋았던 게시물을 few-shot 레퍼런스로 삽입할 블록을 만듭니다.

    빈 문자열이면 아무것도 넣지 않습니다.
    """
    if not top_performers_context:
        return ""

    return (
        f"\n[과거 우수 성과 게시물 레퍼런스]\n{top_performers_context}\n\n"
        "아래의 [과거 우수 성과 게시물 레퍼런스]는 우리 매장에서 반응이 가장 좋았던 홍보물들입니다. "
        "이 텍스트들의 문체, 감성, 길이를 분석하고 모방하여 이번 타겟 시간대와 날씨에 맞는 "
        "새로운 홍보 텍스트를 작성해 주세요.\n"
    )


def format_top_performers(performers: List[dict]) -> str:
    """
    Spring에서 받은 우수 게시물 목록을 사람이 읽는 한 줄 요약들로 변환합니다.

    입력 예시:
        [{"clickCount": 120, "marketingText": "...", "tags": ["카페", "신메뉴"]}, ...]
    """
    lines = []
    for i, performer in enumerate(performers, start=1):
        click_count = performer.get("clickCount", 0)
        marketing_text = performer.get("marketingText", "")
        tags = ", ".join(performer.get("tags", []))
        lines.append(f"우수사례 {i} (클릭수: {click_count}) - 내용: {marketing_text} / 태그: {tags}")
    return "\n".join(lines)


def build_marketing_prompt(
    content_type: str,
    resolved_mode: str,
    vision_section: str = "",
    performers_section: str = "",
) -> str:
    """
    최종 프롬프트 템플릿 문자열을 조립합니다.

    Args:
        content_type: "POST" 또는 "VIDEO".
        resolved_mode: "TRANSFORM" 또는 "ORIGINAL" (AUTO는 이미 해석된 상태여야 함).
        vision_section: `build_vision_section()` 결과 (없으면 빈 문자열).
        performers_section: `build_top_performers_section()` 결과 (없으면 빈 문자열).

    Returns:
        `TEMPLATE_VARIABLES` 를 자리표시자로 갖는 PromptTemplate용 문자열.
    """
    wants_image_prompts = needs_image_prompts(content_type, resolved_mode)

    image_instruction = (
        _IMAGE_PROMPT_INSTRUCTION if wants_image_prompts else _NO_IMAGE_PROMPT_INSTRUCTION
    )
    content_instruction = _CONTENT_TYPE_INSTRUCTIONS.get(content_type, "")

    prompt = f"""당신은 소상공인을 돕는 전문 마케터입니다. 아래 정보를 바탕으로 매력적인 홍보 텍스트를 작성하세요.
{image_instruction}

[실시간 날씨 컨텍스트]
{{weather_data}}

[마케팅 정보]
- 분위기 태그: {{mood_tag}}
- 해시태그: {{hash_tag}}
- 사용자 추가 요청: {{user_prompt}}
- 업로드 예정 요일: {{upload_day}}
- 업로드 예정 시간: {{upload_time}}{vision_section}{performers_section}
중요 지시사항:
1. {content_instruction}
2. "내용 :", "마케팅 문구 :" 등 어떠한 메타 텍스트나 접두사도 절대 포함하지 마세요. 오직 실제 사용될 텍스트만 작성하세요.
3. 홍보 텍스트는 [실시간 날씨 컨텍스트]의 기상 상황을 자연스럽게 반영하여 작성하세요.
4. 업로드 예정 시간({{upload_day}} {{upload_time}})에 맞는 타겟 독자 상황을 고려하세요.
5. 해시태그({{hash_tag}})의 의미만 홍보 텍스트 내용에 자연스럽게 반영하되, 텍스트 내에 '#' 기호나 해시태그 단어 자체는 절대 포함하지 마세요. (나레이션용 문장만 작성)

출력 형식:
(여기에 순수 홍보 텍스트만 작성)
"""

    if wants_image_prompts:
        prompt += _IMAGE_PROMPT_OUTPUT_FORMAT

    return prompt
