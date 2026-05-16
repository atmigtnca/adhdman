# ADHDman

ADHDman은 생각, 할 일, 일정, 애매한 메모를 빠르게 받아 적고 다시 실행 가능한 형태로 정리하는 **로컬 우선 실행 보조 도구**입니다.

복잡한 협업 SaaS가 아니라, 개인이 자기 컴퓨터에서 가볍게 켜고 쓰는 단일 사용자 도구를 목표로 합니다.

## 핵심 특징

- 빠른 캡처: 애매한 문장도 버리지 않고 먼저 inbox에 저장
- 할 일 / 일정 분류: 규칙 기반 분류와 선택적 LLM 분류 지원
- 오늘 할 일 보기: 지금 볼 것만 작게 보여주는 `/today`
- 안전한 수정과 되돌리기: 주요 변경은 action log에 기록되고 `/undo`로 복구 가능
- TUI 명령 센터: 터미널에서 slash command로 조작
- 읽기 전용 Web UI: 브라우저에서 현재 상태를 확인
- 실행 보조 기능: focus, breakdown, stuck reset, body double, MVS, survival mode

## 보안 모델

ADHDman은 의도적으로 **로그인/계정/권한 시스템이 없습니다.**

따라서 직접 public internet에 노출하면 안 됩니다.

권장 사용 방식:

- 기본은 `127.0.0.1` localhost에서만 실행
- 원격 접근이 필요하면 SSH tunnel, VPN, reverse proxy 인증 같은 외부 보호 계층 사용
- 실제 비밀값은 `.env`에만 저장
- SQLite 데이터베이스와 `.env`는 git에 커밋하지 않기

## 실행 방법

가장 간단한 실행 방법은 Docker Compose입니다.

```bash
docker compose up --build
```

기본 compose 설정은 다음 주소로만 바인딩됩니다.

```text
http://127.0.0.1:8000
```

상태 확인:

```bash
curl -s http://127.0.0.1:8000/health
```

Web UI 열기:

```text
http://127.0.0.1:8000/web
```

중지:

```bash
docker compose down
```

로컬 Python 환경에서 직접 실행하고 싶다면:

```bash
DATABASE_PATH=./data/adhdman.sqlite python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

## 설정

`.env.example`을 참고해 로컬 `.env`를 만들 수 있습니다.

```bash
cp .env.example .env
```

대표 설정:

```bash
DATABASE_PATH=./data/adhdman.sqlite
CLASSIFY_ENABLED=true
LOCAL_TIMEZONE=UTC
UNDO_ENABLED=true
```

LLM 분류는 선택 기능입니다. `OPENROUTER_API_KEY`가 없으면 네트워크 호출 없이 규칙 기반 분류와 inbox fallback으로 동작합니다.

## 빠른 시작

처음 실행한다면 아래 순서대로 확인하면 됩니다.

```bash
# 1. 설정 파일 준비
cp .env.example .env

# 2. 서버 실행
docker compose up --build

# 3. 다른 터미널에서 상태 확인
curl -s http://127.0.0.1:8000/health
```

브라우저에서는 아래 주소를 엽니다.

```text
http://127.0.0.1:8000/web
```

Web UI는 읽기 전용입니다. 실제 입력과 변경은 API 또는 TUI에서 합니다.

## 기본 사용 흐름

ADHDman은 보통 아래 흐름으로 사용합니다.

```text
캡처하기 → inbox/tasks/events 확인하기 → 오늘 볼 것 고르기 → focus 걸기 → 완료/undo
```

### 1. 생각 캡처

```bash
curl -s -X POST http://127.0.0.1:8000/capture \
  -H 'Content-Type: application/json' \
  -d '{"text":"내일 오전 10시에 병원 예약"}'
```

캡처된 내용은 먼저 inbox에 저장됩니다. 분류가 가능하면 task/event로 승격되고, 애매하면 inbox에 남습니다.

### 2. inbox 확인

```bash
curl -s http://127.0.0.1:8000/inbox
```

### 3. task 확인

```bash
curl -s http://127.0.0.1:8000/tasks
```

### 4. 오늘 볼 것 확인

```bash
curl -s http://127.0.0.1:8000/today
```

### 5. 완료 처리

```bash
curl -s -X POST http://127.0.0.1:8000/tasks/1/done
```

### 6. 되돌리기

```bash
curl -s -X POST http://127.0.0.1:8000/undo/latest
```

## 사용 예시

### 예시 A. 갑자기 떠오른 메모 저장하기

```bash
curl -s -X POST http://127.0.0.1:8000/capture \
  -H 'Content-Type: application/json' \
  -d '{"text":"전기요금 납부하기"}'
```

기본 설정에서는 애매한 입력이 안전하게 inbox에 남을 수 있습니다.

```bash
curl -s http://127.0.0.1:8000/inbox
```

할 일로 확정하고 싶다면 inbox 항목을 task로 승격합니다.

```bash
curl -s -X POST http://127.0.0.1:8000/inbox/1/promote-task
curl -s http://127.0.0.1:8000/tasks
```

### 예시 B. 일정처럼 보이는 문장 저장하기

```bash
curl -s -X POST http://127.0.0.1:8000/capture \
  -H 'Content-Type: application/json' \
  -d '{"text":"금요일 오후 3시에 병원 예약"}'
```

LLM 분류를 설정했거나 규칙으로 일정이라고 판단되면 event로 생성됩니다. 그렇지 않으면 inbox에 남으므로 나중에 다시 정리할 수 있습니다.

```bash
curl -s http://127.0.0.1:8000/events
curl -s http://127.0.0.1:8000/inbox
```

### 예시 C. 지금 하나만 정해서 시작하기

```bash
curl -s http://127.0.0.1:8000/today
curl -s -X POST http://127.0.0.1:8000/focus/start \
  -H 'Content-Type: application/json' \
  -d '{"target_type":"task","target_id":1}'
```

작업을 끝냈다면 완료 처리합니다.

```bash
curl -s -X POST http://127.0.0.1:8000/tasks/1/done
```

실수했다면 되돌립니다.

```bash
curl -s -X POST http://127.0.0.1:8000/undo/latest
```

### 예시 D. 너무 큰 일을 작게 쪼개기

```bash
curl -s -X POST http://127.0.0.1:8000/tasks/1/breakdown/suggest \
  -H 'Content-Type: application/json' \
  -d '{}'
```

제안을 보고 저장하려면 직접 step을 확정해서 보냅니다.

```bash
curl -s -X POST http://127.0.0.1:8000/tasks/1/breakdown \
  -H 'Content-Type: application/json' \
  -d '{"steps":["파일 열기","첫 줄 쓰기"],"source":"manual"}'
```

### 예시 E. 에너지가 낮을 때 survival mode 켜기

```bash
curl -s -X POST http://127.0.0.1:8000/survival/enter \
  -H 'Content-Type: application/json' \
  -d '{}'
```

이 상태에서는 대시보드가 task/event를 최소 개수만 보여줍니다. 데이터는 삭제되지 않습니다.

끄려면:

```bash
curl -s -X POST http://127.0.0.1:8000/survival/exit \
  -H 'Content-Type: application/json' \
  -d '{}'
```

## TUI 사용

TUI는 터미널에서 ADHDman과 대화하듯 조작하는 메인 인터페이스입니다.

처음 한 번만 로컬 Python 환경에 설치합니다.

```bash
pip install -e ".[tui]"
```

그 다음부터는 `claude`처럼 명령 하나로 실행할 수 있습니다.

```bash
adhdman
```

서버가 8000번이 아닌 다른 포트에서 돌고 있다면 backend 주소를 지정합니다.

```bash
ADHDMAN_BASE_URL=http://127.0.0.1:3000 adhdman
```

소스 설치를 하지 않은 개발 환경에서는 아래 명령도 사용할 수 있습니다.

```bash
python -m tui
```

주요 명령:

```text
/today             지금 볼 것 확인
/inbox             inbox 목록
/tasks             task 목록
/events            event 목록
/search <query>    task/event/inbox 검색
/pick N            검색 결과 N번 선택
/done N            task 목록의 N번 완료
/undo              최근 변경 되돌리기
/focus N           최근 목록의 N번에 집중
/focus stop        집중 종료
/breakdown N       task N번을 작은 단계로 쪼개기 제안
/breakdown commit  제안된 단계 저장
/stuck             막혔을 때 선택지 보기
/stuck shrink      더 작게 쪼개기
/stuck swap        다른 대상으로 전환
/stuck skip        하루 미루기
/stuck park        오늘 목록에서 잠시 숨기기
/body-double N     N초 간격의 로컬 body-double 세션 시작
/body-double check-in
/body-double stop
/mvs N             최소 실행 가능 단계 제안
/mvs commit        제안된 최소 단계 저장 후 focus
/survival on       survival mode 켜기
/survival off      survival mode 끄기
/help              도움말
/quit              종료
```

대상을 바꾸는 명령은 자유문장 대신 목록 번호를 사용합니다. 잘못된 항목을 수정하는 일을 줄이기 위한 설계입니다.

## Web UI

Web UI는 읽기 전용 대시보드입니다.

```text
http://127.0.0.1:8000/web
```

표시하는 내용:

- Now
- Inbox
- Tasks
- Events
- Week
- Recent Changes
- Focus 상태
- Body-double 상태
- Survival mode 상태

Web UI에는 생성/수정/삭제 버튼이 없습니다. 데이터 변경은 API 또는 TUI에서 수행합니다.

## 실행 보조 기능

### Focus

하나의 task/event/inbox item에 집중 상태를 겁니다.

```bash
curl -s -X POST http://127.0.0.1:8000/focus/start \
  -H 'Content-Type: application/json' \
  -d '{"target_type":"task","target_id":1}'
```

### Breakdown

큰 task를 2~5개의 작은 child task로 나눕니다.

```bash
curl -s -X POST http://127.0.0.1:8000/tasks/1/breakdown \
  -H 'Content-Type: application/json' \
  -d '{"steps":["문서 열기","첫 문단 쓰기"],"source":"manual"}'
```

### Stuck reset

막혔을 때 `shrink`, `swap`, `skip`, `park` 중 하나를 적용합니다.

```bash
curl -s -X POST http://127.0.0.1:8000/stuck \
  -H 'Content-Type: application/json' \
  -d '{"target_type":"task","target_id":1,"choice":"shrink"}'
```

### Body double

외부 서비스 없이 로컬 타이머와 check-in 상태만 기록합니다.

```bash
curl -s -X POST http://127.0.0.1:8000/body-double/start \
  -H 'Content-Type: application/json' \
  -d '{"interval_seconds":300}'
```

### MVS

Minimum Viable Step, 즉 지금 시작할 수 있는 가장 작은 한 단계를 만듭니다.

```bash
curl -s -X POST http://127.0.0.1:8000/mvs/suggest \
  -H 'Content-Type: application/json' \
  -d '{"target_type":"task","target_id":1}'
```

### Survival mode

에너지가 낮을 때 화면에 보이는 task/event 수를 최소화합니다. 데이터는 삭제하지 않고 표시만 줄입니다.

```bash
curl -s -X POST http://127.0.0.1:8000/survival/enter \
  -H 'Content-Type: application/json' \
  -d '{}'
```

끄기:

```bash
curl -s -X POST http://127.0.0.1:8000/survival/exit \
  -H 'Content-Type: application/json' \
  -d '{}'
```

## 개발

테스트:

```bash
python -m pytest backend/tests tui/tests -q
```

Lint:

```bash
python -m ruff check backend/app backend/tests tui
```

Docker health check:

```bash
docker compose up --build
curl -s http://127.0.0.1:8000/health
```

## 현재 상태

ADHDman은 아직 개인용 로컬 도구에 가깝습니다. 안정적인 public SaaS나 multi-user 제품이 아닙니다.

public internet에 직접 노출하지 말고, 먼저 localhost에서 사용해 보세요.
