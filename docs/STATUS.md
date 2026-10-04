# 현황 / 세션 기억 (STATUS)
> 공개 저장소라 실제 호스트명은 적지 않는다: `<AGENT_HOST>`(이 Agent), `<SPRING_HOST>`(Spring 콜백), `<OTHER_HOST>`. 실제 값은 `.env`·Cloudflare 대시보드(`curl localhost:20241/config`) 참고.

> 이 파일은 Claude가 세션이 바뀌어도 맥락을 잃지 않도록 유지하는 **작업 기억**이다. 작업할 때마다 갱신한다.
> 프로젝트 자체 설명은 `PROJECT_OVERVIEW.md`, 해야 할 일은 `PLAN.md`, 팀 회의 안건은 `MEETING_AGENDA_1006.md`.

## 목표 (사용자 지시, 2026-10-04)

- 졸업작품 최종 발표용으로 이 서버의 **취약점 대부분을 예외처리 수준으로 해결**한다. 실서비스 수준은 요구하지 않는다. 발표 중 터지지 않는 것이 기준.
- **1차 목표: Agent 서버를 완벽히.** 이후 백엔드 팀이 이 서버를 테스트할 때 잘 제공할 수 있게 준비한다.
- 작업 범위: **현재 기능 그대로 안정화**. Spring 쪽이 함께 바꿔야 하는 것은 하지 않고, 회의 안건 *후보*로 사용자에게 보고한다(안건 파일 추가 여부는 사용자가 결정).
- 권한: `docs/`에 계획·현황·세션 기억을 자유롭게 작성. 커밋은 단위별로, 푸시는 사용자 지시 시에만.

## 확정된 사실·결정 (사용자 답변)

- **호출 구조**: `All-Ligo_Was`(Spring, 다른 백엔드 담당자가 별도 머신에서 운영) → 이 Agent. `All-Ligo_Aos`는 폐기 예정이라 무시. `All-Ligo_Worker`도 작업 대상 아님.
- **테스트는 우리끼리 로컬에서**. 백엔드는 ERD 수정 중이라 실제 Spring 에 의존한 테스트는 하지 않는다. Spring 클론은 10/4 상위 폴더 `../All-Ligo_WAS` 로 받음 — **참고용으로 읽기만**, 수정은 사용자 컨펌 후. 실제 연동 동작은 `WAS_INTEGRATION.md`.
- **발표 시나리오**: 설계과제 때 시연한 흐름(VIDEO+TRANSFORM → YouTube 업로드) 재현.
- **영상 노출 방식**은 팀 논의 → `MEETING_AGENDA_1006.md` #1.
- **연구실 서버에 백엔드 팀원이 접속하는 것은 곤란**. 배포·연결은 사용자가 직접 한다.

## 인프라 사실 (2026-10-04 확인)

| 항목 | 내용 |
|---|---|
| 머신 | i7-13700(24스레드), RAM 62GB, RTX 4090 24GB, `/data` 여유 약 178GB |
| Cloudflare 터널 | `cloudflared.service` (systemd, 토큰 방식, 재부팅 시 자동 시작). 라우팅은 대시보드에 있음. 로컬 조회: `curl localhost:20241/config` |
| 터널 라우팅 | `<AGENT_HOST> → localhost:8000` (**이 Agent**), `<OTHER_HOST> → localhost:8083` (정체 미확인) |
| 웹훅 대상 | `<SPRING_HOST>/api/internal/content-callback` (다른 머신의 Spring) |
| Agent 서버 | **tmux 세션 `agent`**, 18:49 새 코드로 재기동. 로그 `../agent-server.log`. 터널 너머 status/preflight 200 |
| sudo | 비밀번호 필요 → systemd 유닛 설치는 사용자가 직접 |

### 기동·재기동
```bash
tmux attach -t agent                      # 로그 보기 (나올 때 Ctrl+b d)
tmux kill-session -t agent                # 중지
cd /data/cj/fastapi-marketing-agent/All-Ligo_Agent
tmux new-session -d -s agent -c "$PWD" "source ../venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8000 2>&1 | tee -a ../agent-server.log"
curl -s localhost:8000/api/system/preflight   # 기동 점검 결과
```
- tmux는 로그아웃에는 살아남지만 재부팅에는 죽는다 → systemd 유닛 필요(PLAN A1).
- ⚠ Bash에서 `pkill -f "uvicorn app.main:app"` 는 명령 자신을 죽인다(exit 144). tmux 세션 단위로 종료할 것.

### 테스트 방법 (실제 Spring 을 건드리지 않음)
- 단위/스모크: `pytest` (24개, 약 1.4초, 외부 서비스 불필요)
- E2E: 별도 인스턴스 `:8001` + 가짜 웹훅 수신기 `:9999`, S3 비활성
  (`SPRING_WEBHOOK_URL=http://127.0.0.1:9999/cb AWS_S3_BUCKET= uvicorn app.main:app --port 8001`)
  — `.env` 는 이미 설정된 환경 변수를 덮어쓰지 않으므로 이렇게 앞에 붙이면 된다.

## 실측 수치 (2026-10-04, 이 머신)

| 항목 | 값 |
|---|---|
| POST 텍스트 생성 (변경 전 → 후) | 3분 18초(사고 모드 폭주) / 보통 10초 → **4~6초** |
| VIDEO+TRANSFORM 전체 (디스크 캐시 차가울 때) | **91초** (텍스트 4 · SDXL 로드 47 · 이미지 3장 13 · 영상 20) |
| 같은 시나리오 (캐시 따뜻할 때) | **43초** |
| VIDEO+TRANSFORM 2건 동시 | 2건 합계 54초, GPU 최고 15.2GB, 그동안 status 응답 0.04초 이내 |
| llava:13b 이미지 분석 | 첫 로드 39초 / 이후 2초 |
| 기존 구조(장마다 모델 로드)였다면 | SDXL 로드만 약 141초 (47초×3) |

## 발표 리스크 현황

| 리스크 | 상태 |
|---|---|
| FLUX 모델 없음 | 캐시 전용으로 바꿔 즉시 SDXL 사용 (`eb6634d`). FLUX 를 받을지는 사용자 결정 대기 |
| **YouTube refresh token 만료** (`invalid_grant`) | ❗ **미해결 — 사용자가 재발급해야 함** (`python scripts/refresh_youtube_token.py`). 기동 점검이 경고로 알려줌 |
| 서버가 재부팅에 죽음 | tmux 로 로그아웃만 대응. systemd 유닛 미설치 |
| S3 업로드 | 설정·자격 증명은 있음. 실제 업로드는 이번에 시험하지 않음(팀 버킷에 쓰기 발생) |
| cloudflared 가 `X-Forwarded-Proto` 를 보내는지 | 헤더가 있으면 posterUrl 이 https 로 만들어짐을 확인. 실제 터널 트래픽으로는 미확인 |

## 진행 로그

- 2026-10-04 오전: `PROJECT_OVERVIEW.md`, 상위 `WORKSPACE.md` 작성. 터널 점검, 서버 기동.
- 2026-10-04 오후: 안정화 커밋 12개 (`f416a89` ~ `9af89ca`, 상세는 PLAN 및 `git log`). 운영 서버 새 코드로 재기동. README·문서 갱신.
- 의도치 않은 부작용 1건: Spring 콜백 URL 에 빈 `{}` POST 1회(200). 안건 파일 하단에 기록.
- 2026-10-04 저녁: 사용자가 상위 폴더에 `All-Ligo_WAS` 클론 제공. **읽기만** 해서 실제 연동 동작을 `WAS_INTEGRATION.md` 에 정리. Was·Agent 코드 모두 수정 안 함(사용자 지시: 수정은 컨펌 후). 안건 후보를 Was 기준으로 재정리(아래).

## Was 확인 후 기존 안건 재평가 (사용자 컨펌 대기 — 안건 파일은 아직 안 고침)

| 기존 안건 | Was 코드로 확인한 사실 | 제안 |
|---|---|---|
| #1 영상 노출 | 미리보기 응답에 `s3VideoUrl`(생성 직후)·`uploadVideoUrl`(YouTube 후) 둘 다 이미 내려감 | "프론트가 어느 필드를 재생하나" 로 좁히기 |
| #2 `mode` 값 | Was 는 웹훅 `mode` 를 안 쓰고, 요청도 항상 `TRANSFORM` | **안건에서 빼기** |
| #3 인증 | Agent 호출에 헤더 없음. Was 콜백도 무인증 | 유지 (양방향 공유 키로 범위 확대) |
| #4 재시도·중복 | 콜백은 중복 수신해도 결과 동일(Content·알림 중복 없음) | **Agent 단독으로 자동 재시도 구현 가능** → 안건에서 빼고 "공유" 로 |
| #5 동시 요청 | Was 는 status 확인 안 함. T−5분 생성, T 에 업로드, 업로드 시점에 결과 없으면 재시도 없음 | "같은 시각 예약 몇 건까지 시연하나 / T−5분 유지?" 로 바꾸기 |
| #6 ERD | 원격 최신이 06-23 → ERD 수정분 아직 없음 | 유지 |

### 새 안건 후보 (Was 확인으로 구체화됨)

1. **S3 업로드 실패 시 영상 유실** (W1): Was 가 `s3VideoUrl` 이 null 이면 `localVideoPath` 도 저장하지 않아, 영상이 있는데도 YouTube 업로드가 조용히 빠진다. Was 에서 `localVideoPath` 를 항상 저장하면 해결(한 줄 수준).
2. **VIDEO 인데 영상 없는 SUCCESS** (W2): Was 는 `GENERATED` 로 저장 → 대기열에는 정상으로 보이고 업로드는 조용히 빠짐. Agent 가 `FAILED` 로 보낼지, Was 가 VIDEO+영상없음을 처리할지.
3. **`AGENT_SERVER_URL` 실제 값**: VIDEO 썸네일(posterUrl)이 이 주소 기준으로 만들어진다. `https://<AGENT_HOST>` 여야 함.
4. **(Was 쪽, 추정) 실행 상태 덮어쓰기** (W5): 스케줄러 트랜잭션이 길면 콜백의 `SUCCESS` 가 `PROCESSING` 으로 덮여 대기열에 "생성 중" 으로 남을 수 있음. 재현은 안 함.
5. **(Was 쪽) 취소한 콘텐츠도 업로드됨** (W6).
6. YouTube 채널·OAuth 앱 소유자 (토큰 재발급·프로덕션 게시 담당).

이전 후보 중 빠진 것: `/api/vision/analyze` 타임아웃(Was 가 안 씀), FAILED `error` 문구(앱에 노출 안 됨).

### Agent 단독으로 할 수 있게 된 것 (사용자 컨펌 대기)

- **A4 웹훅 자동 재시도**: Was 콜백이 멱등임을 확인 → 연결 오류·5xx 시 백오프 2~3회 재시도.
- **S3 업로드 재시도**: W1 피해를 줄이기 위해 S3 업로드 실패 시 1~2회 재시도.
- **영문 요일 → 한글 변환**: Was 가 `MONDAY` 로 보내 프롬프트에 영문 요일이 들어감. 프롬프트 품질 개선(선택).

## 미해결 / 대기

1. YouTube 토큰 재발급 (사용자)
2. FLUX 사전 다운로드 여부 (사용자 결정, 약 30GB대 디스크·네트워크)
3. systemd 유닛 설치 (sudo, 사용자)
4. ~~Spring 클론 수신 대기~~ → 10/4 수신 (`../All-Ligo_WAS`, 읽기 전용으로만 사용)
5. `<OTHER_HOST> → :8083` 정체
6. 위 "재평가"·"새 안건 후보"·"Agent 단독 작업" 에 대한 사용자 컨펌
