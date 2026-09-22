# 구현·검증 기록

2026-09-12 개발 환경에서 검증했습니다. 실제 EC2와 운영 도메인 검증을 대신하지 않습니다.

| 검증 | 결과 |
|---|---|
| Django 시스템·운영 보안 검사 | 경고 없음, `check --deploy --fail-level WARNING` 통과 |
| PostgreSQL 자동 테스트 | 로컬 환경과 최종 Docker 이미지 내부에서 각각 35개 통과 |
| 정답 예측 RMSE=0, 완전 분리 ROC-AUC=1 | 순수 지표 및 게시→접수→채점→최종 평가 흐름 통과 |
| 순서가 다른 CSV·문자열 ID | 같은 점수, 앞자리 0 유지 |
| CSV·열·ID·NaN/Infinity·범위·데이터 분할 검사 | 한국어 오류, 잘못된 제출은 접수 횟수 제외 |
| 정답·타인 제출·공개 전 test 점수·개인정보 | 접근 및 학생 응답 노출 차단 |
| 관리자 OTP | 인증 전 운영·파일 접근 차단, 인증된 운영 화면 통과 |
| 복구 코드 | 해시 저장, 30분 만료, 재사용·구코드 차단, 기존 세션 삭제 |
| 마감·최종 선택·지연 채점 | 마감 정각 거절, 마감 직전 허용, 대기 작업 완료 후 확정 |
| 20건 중복 요청 | 동일 접수 1건 |
| 한 학생의 20건 동시 요청 | 기본 한도 5건 접수, 15건 제한 |
| 로그인 사용자 100명·20건 동시 업로드 | Django HTTP 테스트 클라이언트와 PostgreSQL, 실행 스레드 20개. 20건 접수·중복 채점 방지 확인 |
| 실제 컨테이너 업로드 | Nginx→Gunicorn→PostgreSQL→Redis→Celery로 CSV 접수·RMSE 0 |
| Nginx 내부 다운로드 | 승인 사용자 다운로드 성공, 내부 경로 직접 호출·원본 접근 404 |
| Redis 손실 | 작업자를 멈춘 후 제출, 전용 Redis 큐 초기화, reconcile 후 실제 작업자로 복구 |
| 중단 작업 lease·재시도 | stale processing 복구, 최초 실행+최대 3회 재시도, 결과 단일 확정 |
| 컨테이너 재시작 | DB·Redis·웹·작업자·Nginx 재시작 후 파일·점수 유지 |
| 다음 학기 복제 | 새 데이터·참가자·제출 없음, 이전 결과 보존 |
| 백업·복원 | 운영 스크립트로 pg_dump+파일 아카이브 생성, 체크섬 검사, 새 DB·빈 디렉터리 복원 |
| 독립 DB 서버 복원 | 별도 PostgreSQL 컨테이너와 별도 앱·파일 디렉터리에서 제출 3건 및 원본·제출 해시 일치 |
| 브라우저 | Chromium 1440px/390px, 회원가입·참가 신청, JS 오류 및 모바일 가로 넘침 없음 |
| Certbot 실행 | 4.0.0 컨테이너 실행 확인, 실제 인증서 발급은 미수행 |
| Nginx TLS 구성 | 임시 자체서명 인증서를 이용한 `nginx -t` 통과 |
| Docker 이미지 | Python 3.13 기반 `chembig:verified` 빌드 성공 |

발견 후 수정한 사항: 테스트 스레드의 DB 연결 정리, 검증 프록시의 Host 포트 전달, 복원 파일의 UID/GID 10001 소유권, 재배포 후 Nginx DNS 재해석을 위한 reload, 서버 오류 환급 제출의 재처리 한도 처리.

서버 오류로 한도를 환급한 접수는 이후 복구해도 새 한도를 차감하지 않습니다. 이 경우 해당 날짜의 성공 결과 수가 설정 한도보다 많을 수 있습니다. 새 제출의 접수 한도는 유지되며 이 예외는 `quota_exempt`에 기록됩니다.

2026-09-21 코드 점검에서 발견·수정한 사항: 초대 코드에 비ASCII 문자가 있으면 참가 신청이 500(`compare_digest`를 UTF-8 바이트로 비교), 관리자 대회 폼에서 시작·마감을 비우면 TypeError 500(`Contest.clean`의 None 방어), 참가 승인 폼에 숫자가 아닌 ID가 오면 500, `DEBUG=0`에서 500 추적 정보가 어디에도 기록되지 않던 기본 로깅(stdout 로깅 설정 추가), 제출 접수 시 대회 전체 행 잠금 안에서 CSV를 두 번 파싱·채점하던 구조(검증·사전 채점을 잠금 밖으로, 파싱 1회), 로그인 제한 카운터 행 무한 누적(reconcile에서 정리), 세부 지표 도입 이전 채점분의 빈 지표(`backfill_metrics` 명령). 자동 테스트는 SQLite·PostgreSQL 각각 50개 통과.

같은 날 2차 점검에서 발견·수정한 사항: 채점기 버전(`SCORER_VERSION`) 상향 시 대기 중이던 제출이 영구 오류가 되고 재처리로도 복구되지 않던 문제(채점 시점의 버전을 기록하고 불일치는 경고 로그로), `isdigit()`이 `²` 같은 유니코드 숫자를 통과시켜 남아 있던 500(`isdecimal()`), 지표 함수의 모든 `ValueError`를 학생 파일 오류로 표시하던 처리(계산 불가 사유만 `MetricUndefined`로 구분, 나머지는 서버 오류로 기록), 로그인 제한 행 정리와 미들웨어 조회 사이의 경쟁으로 로그인이 500이 될 수 있던 문제(잠금 조회·생성을 한 번에), 큰 유한 예측값에서 `np.std`가 오버플로해 정의된 Spearman을 거부하던 상수 판정(min == max), `create_contest`에서 뒤쪽 문제가 실패하면 앞쪽 문제의 데이터 파일이 디스크에 남던 문제(사전 검증 + 실패 시 파일 정리), 날짜 항목이 문자열이 아닐 때의 추적 정보 노출(TypeError 처리), 한도 초과 학생의 CSV를 끝까지 파싱한 뒤 거절하던 순서(파싱 전 사전 확인), 순위 지표 이중 계산과 채점 코드 중복 정리, 화면 반복문 안의 지표 목록 재생성, 세부 지표가 없는 과거 제출의 순위 지표를 `—`로 표시하던 화면. 자동 테스트는 SQLite 56개(동시성 3개 제외)·PostgreSQL 56개 통과.

2026-09-22: 관리자 화면에 대회·문제 삭제가 전혀 없던 점을 보완했습니다. 제출이 없는 대회·문제만 삭제할 수 있고(문제는 진행 중·예정 대회에서만), 삭제는 `arena.services.delete_contest`·`delete_problem`을 거쳐 문제·참가 신청·공지·데이터 파일을 함께 지우고 작업 기록에 남깁니다. 제출이 있으면 관리자 폼의 `삭제` 항목이 사유를 표시하고 버튼이 나타나지 않으며, 확인 화면이 열린 사이 제출이 들어오면 잠금 안의 검사가 거부하고, 반대로 제출 처리 중에 대회·문제가 삭제되면 학생에게 삭제 안내를 보냅니다. 삭제에는 Django 모델 권한(`arena.delete_contest`·`delete_problem`)도 필요합니다. 같은 점검에서 gunicorn 스레드 환경에서 안전하지 않은 `warnings.catch_warnings()`를 채점 경로에서 제거하고, 참고 지표 하나의 내부 오류가 제출 전체를 막지 않도록 로그를 남기고 None으로 저장하게 했습니다. 자동 테스트는 SQLite 63개(동시성 3개 제외)·PostgreSQL 63개 통과.

아직 수행하지 않은 항목: 실제 EC2 사양·기존 포트 확인, DNS 연결·Let's Encrypt 실제 발급/갱신, EC2 호스트 재부팅, 교수자 PC로 원격 백업 사본 다운로드, 실제 수업 데이터 크기로 운영 부하 측정 및 실제 주소에서 인수 테스트. EC2 SSH 대상·키 경로·도메인이 필요합니다.

## 재현

```bash
scripts/test.sh
docker build -t chembig:verified .
scripts/verify-stack-setup.sh
npm install --no-save --package-lock=false playwright@1.58.2
# Playwright Chromium 설치 또는 CHROMIUM_PATH 지정
node scripts/browser-smoke.cjs
python3 scripts/stack-check.py
```

통합 검증 사이트는 `http://127.0.0.1:18080`에만 공개됩니다. `stack-check.py`는 전용 `chembig-e2e` 프로젝트의 Redis를 초기화하고 컨테이너를 재시작합니다. 운영 프로젝트에 사용하지 마세요. 브라우저 검증은 매번 새로운 합성 학생을 생성하므로 참가 승인은 테스트 스크립트에서 수행됩니다. 기본 데모 대회의 30일 기간이 지난 뒤 재검증할 때는 별도 검증 환경을 새로 만드세요.

백업 재현:

```bash
export COMPOSE_FILE=compose.yaml:compose.verify.yaml
export COMPOSE_PROJECT_NAME=chembig-e2e
export COMPOSE_ENV_FILES=artifacts/verify.env
sudo --preserve-env=COMPOSE_FILE,COMPOSE_PROJECT_NAME,COMPOSE_ENV_FILES scripts/backup.sh
sudo --preserve-env=COMPOSE_FILE,COMPOSE_PROJECT_NAME,COMPOSE_ENV_FILES scripts/restore.sh /absolute/backup/TIMESTAMP new_restore_db /absolute/new-data
```

정리할 때 동일 Compose 환경변수를 유지하고 `docker compose down`을 실행합니다. `compose.test.yaml` 테스트 프로젝트는 `docker compose -f compose.test.yaml down`으로 정리합니다. 운영 데이터를 자동 삭제하는 명령은 포함하지 않습니다.
