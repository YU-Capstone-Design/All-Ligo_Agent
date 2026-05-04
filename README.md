# 🚀 Marketing AI Agent (Python Backend)

**로컬 AI 마케팅 에이전트** 프로젝트의 핵심 백엔드 서버(`py/`)입니다.

이곳의 코드는 사용자의 요청을 받아 로컬 AI 모델을 호출하고, 그 결과(텍스트, 이미지, 영상)를 가공하여 API로 제공하는 역할을 담당합니다. FastAPI 프레임워크를 기반으로 제작되었습니다.

## 📂 프로젝트 구조 한눈에 보기

이 디렉토리는 세 가지 주요 모듈로 구성되어 시너지를 냅니다.

- **`main.py` (API 서버 & 오케스트레이터)**
  - FastAPI를 사용해 외부와 통신하는 API 엔드포인트(`generate`, `create-shortform`)를 제공합니다.
  - 사용자의 키워드, 태그, 이미지 등을 입력받아 어떤 AI 기능을 호출할지 결정하고 전체 작업 흐름을 관리하는 **총괄 지휘자** 역할을 합니다.

- **`image_generator.py` (AI 이미지 생성기)**
  - `main.py`의 요청을 받아 로컬 `Stable Diffusion XL (SDXL)` 모델을 사용해 고품질 마케팅 이미지를 생성합니다.
  - Hugging Face의 `diffusers` 라이브러리를 활용하며, VRAM 최적화를 위한 `cpu_offload` 기능이 적용되어 있습니다.

- **`video_generator.py` (AI 숏폼 영상 제작기)**
  - `main.py`나 사용자가 제공한 이미지를 바탕으로 전문가 수준의 숏폼 영상(9:16 비율)을 제작합니다.
  - `FFmpeg`라는 강력한 미디어 처리 도구를 사용하여 Ken Burns 효과(줌/패닝), 화면 전환, 동적 자막 삽입 등 복잡한 영상 편집을 자동화합니다.

## 🛠️ 로컬에서 실행해보기 (Step-by-Step)

이 프로젝트를 당신의 컴퓨터에서 실행하기 위한 단계별 가이드입니다.

### 1. 사전 준비: 필수 프로그램 설치

먼저, 영상 제작에 필요한 `FFmpeg`와 한글 폰트를 설치합니다.

```bash
# Ubuntu/Debian 기반 시스템 예시
sudo apt update && sudo apt install -y ffmpeg fonts-noto-cjk
```

### 2. 프로젝트 설정

저장소를 클론하고 `py` 디렉토리로 이동한 뒤, 파이썬 가상환경을 설정하고 필요한 라이브러리를 설치합니다.

```bash
# 1. 이 디렉토리로 이동
cd fastapi-marketing-agent/py

# 2. 가상환경 생성 및 활성화
python3 -m venv venv
source venv/bin/activate

# 3. 파이썬 라이브러리 설치
pip install -r requirements.txt
```

### 3. 필수 디렉토리 생성

AI가 생성한 이미지나 영상, 사용자가 업로드한 파일을 저장할 공간이 필요합니다. 아래 명령어로 필수 폴더들을 생성해주세요. 이 폴더들은 `.gitignore`에 등록되어 있어 버전 관리에서 제외됩니다.

```bash
mkdir -p static/images static/videos static/uploads fonts my_photos
```

- `static/images`: AI가 생성한 이미지가 저장됩니다.
- `static/videos`: AI가 제작한 영상이 저장됩니다.
- `static/uploads`: 사용자가 API를 통해 업로드한 파일이 임시로 보관됩니다.
- `fonts`: 영상 자막에 사용할 커스텀 폰트(예: `Pretendard-ExtraBold.otf`)를 이곳에 넣어주세요.
- `my_photos`: 테스트용 개인 사진을 보관하는 폴더입니다.

### 4. 서버 실행!

이제 모든 준비가 끝났습니다. 아래 명령어로 서버를 실행하세요.

```bash
# 개발용 (코드 변경 시 자동 재시작)
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 또는 간단한 실행
python main.py
```

서버가 성공적으로 실행되면, `http://localhost:8000/docs` 에서 사용 가능한 API 목록을 확인하고 직접 테스트해볼 수 있습니다.

## ⚠️ 실행 전 확인사항

- **Ollama 서버**: 텍스트 생성을 위해 로컬 `Ollama` 서버가 실행 중이어야 합니다. (`http://localhost:11434`)
- **Hugging Face 모델 캐시**: 이미지 생성에 필요한 SDXL 모델은 최초 실행 시 자동으로 다운로드됩니다. (`~/.cache/huggingface/`) 충분한 디스크 공간을 확보해주세요.
- **GPU 사양**: 원활한 AI 모델 구동을 위해 **NVIDIA RTX 4080급(VRAM 16GB 이상) GPU**를 권장합니다. 메모리가 부족할 경우 모델 로딩에 실패할 수 있습니다.

---

더 자세한 프로젝트의 비전, 전체 아키텍처, 향후 계획 등은 [상위 README.md](../README.md)를 참고해주세요.
