# Was(Spring) ↔ Agent 연동 실제 동작 정리

> 작성 2026-10-04. 상위 폴더 `All-Ligo_WAS` 클론(`develop` = `03cf98e`, 마지막 커밋 2026-06-23)을 **읽기만 하고** 정리했다. Was 코드는 수정하지 않았다.
> ERD 수정분은 아직 원격에 올라오지 않은 상태(원격 최신 커밋도 06-23)라, 아래 내용은 그 이전 기준이다.
> 경로는 `All-Ligo_WAS/src/main/java/yu/likelion14th/allligo_was/` 기준 상대 경로.

## 1. 호출 흐름

| 시점 | Was 동작 | Agent 엔드포인트 | 근거 |
|---|---|---|---|
| 매분 | 2분마다 다음 7일치 실행(PromotionExecution, `PENDING`)을 스케줄별로 1건씩 만든다 | – | `domains/promotion/service/PromotionExecutionGeneratorScheduler.java` |
| **T − 5분** (코드상 "테스트 로직", 운영은 T − 1시간 예정) | `PENDING` 실행을 `PROCESSING` 으로 바꾸고 생성 요청 | `POST /api/marketing/generate` | `fastapi/service/FastapiScheduler.java:59-193` |
| 작업 완료 시 | 웹훅 수신 → 실행 상태 갱신, Content 생성, 알림 | (Agent → Was) `/api/internal/content-callback` | `fastapi/service/ContentCallbackService.java` |
| **T** | 해당 실행의 Content 에 `localVideoPath` 가 있으면 YouTube 업로드 요청 | `POST /api/marketing/upload` | `FastapiScheduler.java:196-284` |

- Was 가 쓰는 Agent API 는 **`/generate` 와 `/upload` 둘뿐**이다. `/api/system/status`, `/api/vision/analyze`, `/api/weather` 는 호출하지 않는다.
- Agent 주소는 `agent.server.url` = 환경 변수 `AGENT_SERVER_URL` (기본 `http://localhost:8000`). 실제 값은 Was 운영 머신에만 있다.
- RestTemplate: 연결 10초, 읽기 5분 (`fastapi/config/RestTemplateConfig.java`). `/upload` 가 동기 YouTube 업로드여도 충분하다.
- 인증 헤더는 보내지 않는다. 콜백 엔드포인트도 인증이 없다.

## 2. 생성 요청에 실제로 들어가는 값

| 필드 | 값 | 비고 |
|---|---|---|
| `mode` | **항상 `TRANSFORM`** | 업로드 사진은 LLaVA 분석에만 쓰고, VIDEO 는 SDXL 로 새 이미지 3장을 만든다(주석에 의도 명시) |
| `contentType` | 홍보(Promotion)의 `POST`/`VIDEO` (생성·수정 시 둘만 허용) | |
| `imageUrls` | 홍보 이미지 URL 목록 (프론트가 presigned PUT 으로 S3 에 올린 뒤 `base-url + key` 로 저장) | Agent 는 이 URL 을 GET 으로 받으므로 버킷이 공개 읽기여야 함 |
| `moodTag` | 홍보의 `mode` 컬럼(밝음/따뜻함 등), 없으면 `"밝은, 쾌활한"` | |
| `hashTag` | 홍보 태그를 `#` 붙여 공백으로 이은 문자열 | |
| `uploadDay` | 스케줄의 `dayOfWeek` (예: `MONDAY`, 영문 요일 — `DayOfWeek.valueOf` 로 파싱되는 값) | 프롬프트에 영문 요일이 그대로 들어감 |
| `uploadTime` | `HH:mm` | |
| `scheduleId` | 문자열 | |
| `topPerformers` | `[{clickCount, marketingText(최대 500자), tags:[...]}]` JSON 문자열 | 과거 caption/bodyText 가 그대로 들어감 → 10/4 중괄호 수정이 실제로 의미 있음 |
| `lat`/`lon` | **보내지 않음** | 날씨는 항상 "날씨 정보 없음(기본 맑음)" 으로 들어간다 |

## 3. 웹훅 수신 처리 (`ContentCallbackService.processWebhook`)

1. `taskId` 로 실행을 찾고, 없으면 `scheduleId` 의 가장 최근(`executedAt` 내림차순) 실행으로 대체한다.
2. 실행을 못 찾아도 **200 을 돌려준다**(로그만 남김). → Agent 는 이 경우를 실패로 감지할 수 없다.
3. `execution.status` 를 웹훅의 `status` 로 덮어쓴다. `FAILED` 면 `errorMessage` 저장(이 값을 노출하는 API 는 없음).
4. `SUCCESS` 면 실행에 붙은 Content 를 찾거나 새로 만들고:
   - POST → `bodyText`, VIDEO → `caption` 에 `generatedText`
   - `posterUrl` 저장
   - **`s3VideoUrl` 이 null 이 아닐 때만** `s3VideoUrl` 과 `localVideoPath` 를 저장
   - `status = GENERATED`, 생성 완료 알림(같은 Content 에 알림이 있으면 다시 만들지 않음)
5. 웹훅의 `data.mode` 는 **어디에도 쓰지 않는다**.

### 같은 웹훅이 두 번 오면
Content 는 실행당 하나로 다시 찾아 갱신하고, 알림은 중복 생성하지 않는다 → **중복 수신해도 결과가 같다(멱등)**.

## 4. 앱에 보이는 값 (`ContentPreviewResDto`)

`posterUrl`, `bodyText`, `caption`, `s3VideoUrl`, `localVideoPath`, `uploadVideoUrl`(YouTube 업로드 후) 를 모두 내려준다.
→ 영상 재생에 `s3VideoUrl`(생성 직후) 과 `uploadVideoUrl`(T 시점 업로드 후) 둘 다 이미 쓸 수 있다. 어느 쪽을 재생할지는 **프론트 결정**.
→ VIDEO+TRANSFORM 의 `posterUrl` 은 S3 가 아니라 **Agent 서버의 `/static/images/...`** 다. 썸네일이 Agent 서버 가동 여부와 `AGENT_SERVER_URL` 값(http/https)에 달려 있다.

대기열(`PromotionQueueService`)은 실행 상태가 `PENDING`/`PROCESSING`/`FAILED` 면 그대로, `SUCCESS` 면 Content 상태(`GENERATED` 등)로 보여준다.

## 5. Agent 쪽에서 본 영향 (10/4 기준)

| # | 관찰 | Agent 에 미치는 영향 |
|---|---|---|
| W1 | S3 업로드가 실패하면 Was 가 `localVideoPath` 도 버린다 | 영상은 Agent 에 있는데 T 시점 YouTube 업로드가 조용히 건너뛰어지고, 앱에도 영상이 안 나온다 |
| W2 | VIDEO 인데 영상이 없는 SUCCESS 를 받으면 `GENERATED` 로 저장 | 대기열에는 정상 생성으로 보이고, T 시점 업로드는 조용히 건너뜀 |
| W3 | T 시점(Track B)에 Content 가 아직 없으면 그 실행은 다시 시도하지 않는다 | Agent 처리가 5분 안에 끝나야 한다. 실측 1건 43~91초, 동시에 들어오면 GPU 에서 순서대로 처리 |
| W4 | `/api/system/status` 를 확인하지 않고 보낸다 | 동시 요청 제어는 Agent 의 GPU 잠금(`eb6634d`)뿐 |
| W5 | (코드상 추정, 미재현) 생성 요청 스케줄러가 HTTP 호출과 YouTube 업로드를 하나의 트랜잭션 안에서 한다. 같은 회차의 다른 작업 때문에 커밋이 늦어지는 사이 웹훅이 먼저 도착하면, 콜백이 쓴 `SUCCESS` 를 스케줄러 커밋이 `PROCESSING` 으로 덮어쓸 수 있다 | Agent 가 빨라질수록(POST 6~15초) 확률이 오른다. 대기열에 계속 "생성 중" 으로 남는 증상 |
| W6 | 취소(`CANCELLED`)된 Content 도 T 시점 업로드 대상에서 빠지지 않는다(상태 확인 없음) | Agent 와 무관한 Was 동작. 취소를 시연한다면 확인 필요 |
| W7 | `lat`/`lon` 을 보내지 않는다 | 날씨 연동 기능이 실제로는 쓰이지 않는다 |
