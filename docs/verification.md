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
