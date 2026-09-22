# ChemBIG ADMET Challenge

기존 EC2 한 대에서 운영하는 한국어 개인전 CSV 예측 대회 플랫폼입니다. Django 5.2 LTS, PostgreSQL, Redis, Celery, Gunicorn, Nginx, Certbot을 사용합니다. 외부 SaaS·CDN·이메일 서비스 없이 동작합니다. AWS 인프라 사용료는 별도입니다.

## 로컬 시작

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
export DEBUG=1
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo
.venv/bin/python manage.py runserver 0.0.0.0:8000
```

`http://localhost:8000`에서 확인합니다. 데모는 합성 데이터이며 계정을 자동 생성하지 않습니다. 초대 코드는 `CHEMBIG-DEMO`입니다. SQLite 모드는 화면 확인용이며 동시성 검증·실제 운영에는 PostgreSQL이 필요합니다.

관리자 생성과 OTP 등록:

```bash
DEBUG=1 .venv/bin/python manage.py createsuperuser
DEBUG=1 .venv/bin/python manage.py reset_admin_otp 관리자ID
```

출력되는 `otpauth://` 비밀 URL을 인증기에 등록하세요. `/admin/login/`에서 ID·비밀번호·OTP로 로그인하면 `/ops/`에서 운영할 수 있습니다. 학생 비밀번호 복구는 이메일 없이 관리자가 발급한 30분 일회용 코드로 처리합니다.

## 개발·검증

```bash
npm ci
npm run build:css
scripts/test.sh
```

CSS와 HTMX는 저장소에 포함되어 서버에서 직접 제공합니다. 폰트는 OS 기본 한국어 글꼴을 사용합니다. `scripts/test.sh`는 로컬 전용 PostgreSQL(55432)과 Redis(56379)를 띄워 테스트합니다. 테스트 후 `docker compose -f compose.test.yaml down`으로 정리할 수 있습니다. 테스트 DB는 운영 DB와 분리됩니다.

```bash
docker build -t chembig:검증한버전 .
docker save chembig:검증한버전 | gzip > chembig-image.tar.gz
```

EC2에서는 검증한 이미지를 `docker load`로 받아 사용합니다. 상세 절차는 [배포·운영 매뉴얼](docs/operations.md), [학생 안내](docs/student.md), [검증 기록](docs/verification.md)을 확인하세요.

## 구현 범위

- 학년도·학기별 대회, 다중 문제, 참가 승인·정지, 별명·실명 선택, 공지와 작업 기록
- 회귀 RMSE·MAE·MSE·R²·Pearson·Spearman, 이진분류 ROC-AUC·AUPRC·Log Loss·Accuracy·Balanced Accuracy·F1·MCC·Precision·Recall·Specificity 지표 레지스트리(`arena/scoring.py`), 문제별 순위 지표 1개 + 같은 유형의 나머지 지표를 참고값으로 자동 계산·저장, ID 기반 순서 독립 채점
- `manage.py create_contest spec.json`으로 CSV·JSON 스펙에서 대회·문제 생성·게시, `scripts/fetch_tdc.py`로 TDC ADMET 벤치마크 과제를 플랫폼 CSV로 변환
- 데이터 검사·학생용 파일 미리보기·게시 후 평가 설정 고정, 제출이 없는 대회·문제만 관리자 화면에서 삭제(데이터 파일·작업 기록 포함)
- 학생·문제별 일일 한도, 요청 UUID에 의한 중복 방지, DB 상태 기반 채점 복구
- 문제별 최고 val 순위, 최종 선택/자동 선택, 마감 후 test 채점·관리자 공개
- OTP 관리자 인증, 해시 저장 일회용 학생 복구 코드와 기존 세션 해제
- Nginx 내부 다운로드, DB·Redis 내부망, 영구 저장, 백업·복원·교수자 PC 다운로드 스크립트

게시·원본·제출 경로는 `DATA_ROOT` 아래에서 분리됩니다. 정답 원본과 백업은 Nginx에 마운트하지 않습니다. 학생 데이터와 제출 파일만 권한 확인 후 내부 다운로드로 제공합니다. 공개 전 test 점수는 학생 응답·CSV 내보내기에 포함하지 않습니다.

실제 EC2 접속 정보·도메인이 제공되지 않아 원격 배포·실제 도메인 인증서 발급·EC2 재부팅 검증은 수행하지 않았습니다.
