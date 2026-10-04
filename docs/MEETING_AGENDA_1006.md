# 10/6 팀 회의 — Agent 서버 관련 결정 안건
> 공개 저장소라 실제 호스트명은 적지 않는다: `<AGENT_HOST>`(이 Agent), `<SPRING_HOST>`(Spring 콜백), `<OTHER_HOST>`. 실제 값은 `.env`·Cloudflare 대시보드(`curl localhost:20241/config`) 참고.

> 작성: 2026-10-04. 회의에서 정해야 할 것을 **하나씩 적재**하는 파일이다. 새 안건은 아래에 번호를 이어서 추가한다.
> 각 안건의 "사실"은 코드/서버에서 직접 확인한 것, "미확인"은 아직 확인 못 한 것이다. 결정되면 `결정:` 줄을 채운다.
> 호출 구조: **Spring(`All-Ligo_Was`) → Agent(이 서버)**, 결과는 Agent → Spring 웹훅. (`All-Ligo_Aos`는 폐기 예정이라 제외)

---

## 1. 생성된 영상을 "바로 보이게" 하는 방식

**배경**: 설계과제 발표에서 교수님이 영상이 (지도 앱처럼) 바로 보이게 하라고 지적하심. 요구는 "보이게만" 하라는 수준이고, 방식은 정해지지 않음.

| 방안 | 흐름 | 사실 | 장점 / 주의 |
|---|---|---|---|
| **A. S3 URL 직접 재생** | Agent가 mp4를 S3에 올리고 웹훅 `s3VideoUrl`로 전달 → 앱/웹 `<video>`가 URL 재생 | 업로드 시 `ContentType=video/mp4` 지정됨. 영상은 H.264+yuv420p+faststart(즉시 재생 형식). **미확인: 버킷 공개 읽기 / CORS** | 구현 이미 됨, 별도 업로드 단계 불필요. 버킷 공개 설정 필요 |
| **B. YouTube 업로드 후 링크/임베드** | 확정된 영상을 `/api/marketing/upload`로 YouTube에 올리고 그 링크(또는 embed)를 앱에서 재생 | 업로드는 항상 `unlisted` 고정(링크 가진 사람만 재생, 임베드 가능, 검색 노출 없음). **미확인: 현재 `token.json` 유효성** | 재생 인프라/대역폭을 YouTube가 부담. YouTube 계정·토큰 관리 필요, 업로드 지연 |
| **C. Agent 서버가 직접 서빙** (참고) | 웹훅의 `localVideoPath` → `https://<AGENT_HOST>/static/videos/...` | 지금도 동작함: `206 Partial Content`, `video/mp4`, Range 지원 확인 | AWS 없이 가능. 이 머신 가동/회선에 의존 |

**논의할 것**
- 방안 A·B 중 무엇을 "바로 보이는" 기본 경로로 할지 (둘 다 쓰면 어떤 시점에 무엇을 앱에 보여줄지).
- 앱/웹이 재생에 쓸 필드: `s3VideoUrl`만? YouTube 링크도 웹훅/DB에 저장해 내려줄지 (현재 `/upload` 응답에 YouTube 링크가 있는지 Spring 쪽과 맞출 것).
- YouTube를 `unlisted` 그대로 둘지 `public`으로 할지.

결정: _(미정)_

---

## 2. 웹훅 `mode` 값

**사실**: 웹훅 `data.mode`는 요청 원본 값이다. 요청이 `AUTO`면 `AUTO`가 그대로 나가고, 실제 적용된 모드(`ORIGINAL`/`TRANSFORM`)는 알 수 없다. 스키마 설명은 "TRANSFORM 또는 ORIGINAL"로 되어 있어 불일치.

**논의**: Spring/DB가 `AUTO`를 저장할 수 있는가, 아니면 해석된 값(`ORIGINAL`/`TRANSFORM`)을 받아야 하는가.
결정: _(미정)_

---

## 3. 서버 간 인증 (선택 사항)

**사실**: Agent의 모든 API는 인증이 없다. Cloudflare 터널의 공개 호스트(`<AGENT_HOST>`)는 인터넷에서 누구나 접근 가능하며, 같은 상위 도메인 아래 서브도메인이라는 사실은 보안 경계가 되지 않는다. `/docs`도 공개라 엔드포인트가 노출된다.

**제안**: Agent는 환경변수 `AGENT_API_KEY`가 **설정된 경우에만** `X-API-Key` 헤더를 검사 (미설정이면 지금처럼 무인증 → 기존 연동이 깨지지 않음). 적용하려면 Spring이 `generate`/`upload` 호출 시 헤더를 추가해야 함.

**논의**: 발표 전에 적용할지, 하지 않을지. 적용하면 Spring 쪽 헤더 추가 가능한가.
결정: _(미정)_

---

## 4. 웹훅 재시도와 중복 수신

**사실**: 지금은 웹훅을 **1회만** 보내고 실패하면 로그만 남긴다(결과 유실). Agent에 재시도(3회 안팎, 백오프)를 넣을 예정.

**논의**: 재시도하면 같은 `taskId` 콜백이 **중복 도착**할 수 있다. Spring 콜백 처리는 `taskId` 기준으로 멱등(두 번 받아도 한 번만 반영)한가?
결정: _(미정)_

---

## 5. 동시 요청 처리 계약

**사실**: 지금은 `/api/system/status`가 `busy`여도 `generate`를 거절하지 않고 모두 실행(GPU 경합 위험). Agent에서 GPU 작업을 1~2개만 동시에 실행하고 나머지는 **큐 대기**시키려 한다. 응답은 계속 즉시 `202`.

**논의**: Spring이 콜백을 기다리는 최대 시간(타임아웃)은? 큐가 길어지면 콜백이 늦어지는데 괜찮은가. (영상 1건 생성 시간은 아직 실측 전)
결정: _(미정)_

---

## 6. ERD 변경이 Agent 계약에 미치는 영향

**사실**: 백엔드 ERD가 수정 중. Agent가 주고받는 필드: 요청 `moodTag, hashTag, prompt, uploadDay, uploadTime, scheduleId, lat/lon, contentType, mode, imageUrls, topPerformers`, 웹훅 `taskId, scheduleId, status, jobType, data{contentType, mode, generatedText, posterUrl, s3VideoUrl, localVideoPath, uploadSchedule, createdAtMillis}`.

**논의**: ERD 변경으로 이 필드명/타입이 바뀌면 **Agent 담당(우리)에게 먼저 공유**. (Agent는 camelCase/snake_case 둘 다 받으므로 요청 쪽은 유연함)
결정: _(미정)_

---

### 참고 (안건 아님)
- 2026-10-04에 Agent 담당이 Spring 콜백 URL 생존 확인을 위해 `<SPRING_HOST>/api/internal/content-callback`에 **빈 JSON(`{}`) POST를 1회** 보냈음(응답 200). Spring 로그에 빈 콜백이 남아 있을 수 있음.
