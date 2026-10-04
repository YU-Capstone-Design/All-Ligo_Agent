"""LLM 입출력 가공(프롬프트 조립, 응답 정리, 이미지 분석 파싱) 테스트."""

from langchain_core.prompts import PromptTemplate

from app.prompts import marketing as mp
from app.services import text_cleaner
from app.services.vision_service import _parse_analysis_text

TEMPLATE_VALUES = dict(
    weather_data="맑음", mood_tag="따뜻한", hash_tag="#카페",
    user_prompt="밤라떼", upload_day="월요일", upload_time="18:00",
)


def _render(prompt: str) -> str:
    return PromptTemplate.from_template(prompt).invoke(TEMPLATE_VALUES).to_string()


def test_외부_입력의_중괄호가_템플릿을_깨지_않는다():
    performers = mp.format_top_performers(
        [{"clickCount": 1, "marketingText": "이벤트 {1+1} 진행", "tags": ["카페"]}]
    )
    vision = mp.build_vision_section({"objects": ["cup {x}"], "mood": [], "colors": []})
    prompt = mp.build_marketing_prompt(
        "POST", "ORIGINAL",
        vision_section=vision,
        performers_section=mp.build_top_performers_section(performers),
    )

    rendered = _render(prompt)

    assert "이벤트 {1+1} 진행" in rendered
    assert "cup {x}" in rendered


def test_tags가_문자열이어도_글자단위로_쪼개지지_않는다():
    text = mp.format_top_performers([{"marketingText": "a", "tags": "카페"}, "깨진 항목"])
    assert "태그: 카페" in text
    assert "카, 페" not in text


def test_분석_결과가_비면_비전_섹션을_넣지_않는다():
    assert mp.build_vision_section({"objects": [], "mood": [], "colors": [], "error": "x"}) == ""


def test_LLaVA_응답이_공백이나_마크다운으로_시작해도_파싱된다():
    parsed = _parse_analysis_text(" Objects: coffee, nuts\n**Mood:** cozy\n- COLORS: brown,")
    assert parsed == {"objects": ["coffee", "nuts"], "mood": ["cozy"], "colors": ["brown"]}


def test_이미지_프롬프트_추출과_본문_정리():
    raw = "마케팅 문구: 따뜻한 하루 #카페\n[IMAGE_PROMPT_1]: a cup\n[IMAGE_PROMPT_2]: a cake"
    assert text_cleaner.extract_image_prompts(raw) == ["a cup", "a cake"]
    assert text_cleaner.clean_marketing_text(raw) == "따뜻한 하루"


def test_자막용_이모지_제거():
    assert text_cleaner.strip_emoji("충전! ✨ 오늘 ☕️ 한잔 👨‍👩‍👧\n둘째 🍂") == "충전! 오늘 한잔\n둘째"
