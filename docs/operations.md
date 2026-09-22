# 관리자·서버 운영 매뉴얼

## 운영 시작

`/admin/login/`에서 OTP 인증 후 `/ops/`를 엽니다. 운영 기능은 활성 staff 계정 중 OTP 인증을 마친 사용자에게만 허용됩니다. 운영자는 학생 실명·학번과 성적을 볼 수 있으므로 교수자에게만 부여하세요.

1. 설정 화면에서 학년도·학기를 만듭니다.
2. 대회를 생성하고 소개, 규칙, 시작·마감(KST), 일일 한도, 초대 코드를 입력합니다. `대회 표시`를 켜면 소개·일정이 공개됩니다.
3. 문제를 추가합니다. 하나의 예측 대상과 순위 지표 하나를 선택합니다. 회귀는 RMSE·MAE·MSE(낮을수록 좋음)·R²·Pearson r·Spearman ρ(높을수록 좋음), 이진분류는 ROC-AUC·AUPRC·Accuracy·Balanced Accuracy·F1·MCC·Precision·Recall·Specificity(높을수록 좋음)·Log Loss(낮을수록 좋음) 중에서 고릅니다. 같은 유형의 나머지 지표는 제출마다 자동 계산되어 내 제출·순위표·성적 CSV에 참고값으로 표시됩니다. 분류의 라벨 지표는 확률 0.5 임계값을 사용합니다. 단위·변환·출처·분할 방법을 모두 기록합니다.
4. ID·SMILES·정답 열과 공개 특징 열의 JSON 배열(예: `["molecular_weight"]`)을 지정합니다. ID는 학생 파일에서 `sample_id`로 통일됩니다.
5. 데이터 관리 링크에서 정답이 들어 있는 train·val·test CSV를 올립니다. 현재 관리 업로드도 파일별 10MB입니다. 필수 열·ID·정답·분류 두 클래스 구성을 검사합니다. 동일 SMILES의 분할 간 중복은 경고하며 화학 구조 정규화에 의한 동등성까지 판단하지는 않습니다.
6. 미리보기에서 val·test에 정답이나 숨겨야 할 특징이 없는지 확인하고 게시합니다. 게시 시 설정과 데이터를 다시 검사하고 고정합니다. 수정하려면 새 문제를 만드세요.
7. 수업 연락 수단으로 초대 코드를 공유하고 참가 신청을 일괄 승인합니다. 정지된 참가자는 데이터·제출·순위표에 접근하지 못하며 순위에서 제외됩니다.
8. 마감 후 대기 채점과 서버 오류를 확인합니다. `최종 제출 확정·test 채점`을 실행한 다음 `최종 결과 공개`를 누릅니다. 확정과 공개는 별도 작업입니다.
9. 성적 CSV에는 이름·학번·별명·문제별 순위·점수·접수 횟수가 포함됩니다. 최종 결과 공개 전에는 val 기준입니다. 수식으로 해석될 수 있는 문자열은 이스케이프합니다.
10. 다음 학기로 복제하면 설명·규칙·문제 설정만 복제되고, 대회는 비공개 초안으로 생성됩니다. 날짜와 새 초대 코드를 확인하고 데이터를 다시 등록하세요. 이전 참가자·제출·결과는 보존됩니다.

종료된 대회와 게시된 문제의 설정은 관리자 폼에서 변경할 수 없습니다. 공지·계정 설정은 계속 운영할 수 있습니다. 데이터와 제출은 고유 경로에 새로 쓰며 덮어쓰지 않습니다. DB의 `dataset_version`은 제출 당시 데이터 버전을, `scorer_version`은 실제 채점에 사용한 채점기 버전을 기록합니다. 직접 SQL로 게시 데이터를 바꾸지 마세요.

삭제는 제출이 하나도 없는 대회·문제에만 허용됩니다. 관리자 대회 화면의 `삭제` 항목이 가능 여부와 사유를 표시하고, 가능한 경우에만 화면 아래 삭제 버튼과 운영 화면의 `삭제` 링크가 나타납니다. 대회 삭제는 문제·참가 신청·공지와 각 문제의 데이터 파일(`originals/<문제 ID>`, `student/<문제 ID>`)을 함께 지우고 작업 기록에 남깁니다. 문제 삭제는 진행 중이거나 예정된 대회에서만 가능합니다. 제출이 있는 대회는 삭제할 수 없으니 보관하려면 `대회 표시`를 끄세요. 목록에서 여러 항목을 한꺼번에 지우는 기능은 없습니다.

## 계정 복구

학생: 본인 확인 → `/ops/recovery/`에서 ID로 코드 발급 → 기존 수업 연락 수단으로 전달. 코드는 30분 유효하며 해시만 저장합니다. 재발급은 이전 코드를 폐기합니다. 성공하면 모든 기존 세션을 삭제합니다. 발급·복구 기록은 남지만 코드는 감사 로그에 남지 않습니다.

관리자: SSH 접속 권한을 가진 운영자가 아래를 실행합니다. 학생 복구 화면은 관리자 계정을 복구하지 않습니다.

```bash
docker compose exec web python manage.py changepassword 관리자ID
docker compose exec web python manage.py reset_admin_otp 관리자ID
```

새 비밀 URL을 인증기 앱에 등록합니다. 기존 OTP 장치와 로그인 세션은 해제됩니다. 서버 시각은 NTP로 동기화하세요.

## EC2 최초 배포

기존 서비스와 충돌하지 않도록 먼저 `ss -lntp`, `docker ps`, `free -h`, `df -h`로 포트·메모리·디스크를 확인합니다. 권장 2 vCPU/4GB RAM/여유 디스크 40GB 이상입니다. 현재 검증 환경의 부하 결과를 EC2 처리량 보장으로 해석하지 마세요.

보안 그룹: TCP 80·443 공개, SSH 22는 운영자 IP 범위로 제한. 5432·6379·8000은 공개하지 않습니다. 도메인의 A 레코드를 EC2 주소로 설정합니다. IPv6를 실제로 서비스하지 않으면 AAAA 레코드를 설정하지 않습니다.

`uname -m`으로 EC2 CPU 아키텍처를 확인하고 해당 아키텍처로 이미지를 빌드하세요. PostgreSQL·Redis·Nginx 이미지는 검증한 digest로 고정했습니다.

Docker Engine과 Compose 플러그인이 설치된 서버에 소스와 **개발 환경에서 빌드·검증한 이미지**를 옮깁니다.

```bash
cd /opt/chembig
cp .env.example .env
chmod 600 .env
# .env의 SECRET_KEY, POSTGRES_PASSWORD를 서로 다른 난수로 교체하고 도메인·이미지 태그를 지정
gunzip -c chembig-image.tar.gz | docker load
scripts/deploy.sh
scripts/init-https.sh challenge.example.ac.kr
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py reset_admin_otp 관리자ID
```

`.env`의 `ALLOWED_HOSTS`에는 도메인 외에 healthcheck용 `localhost`를 유지합니다. `CSRF_TRUSTED_ORIGINS`는 `https://도메인`입니다. `DEBUG=0`을 유지합니다. 최초 Nginx는 ACME 경로 외에는 503을 반환하고 인증서 발급 후 HTTPS 서비스를 시작합니다. Certbot은 이메일 없이 무료 인증서를 발급하도록 구성했습니다. 발급·갱신은 도메인 DNS와 80번 포트 접근이 필요합니다.

`deploy/cron.example`의 설치 경로를 수정해 root 또는 Docker·DATA_DIR 접근 권한이 있는 운영자 crontab에 등록합니다. `deploy/logrotate.conf`를 `/etc/logrotate.d/chembig`에 설치합니다. `sudo systemctl enable --now docker cron`으로 부팅 시 시작되게 합니다. Compose 서비스는 `restart: unless-stopped`입니다. `docker compose down -v`는 영구 볼륨을 삭제하므로 운영 절차에 사용하지 않습니다.

파일 권한: 앱 UID/GID 10001, DATA_ROOT·originals·backups는 750. Nginx에 직접 마운트하는 student·submissions 루트는 755, 파일은 644로 읽을 수 있어야 합니다. Nginx 내부 경로는 외부 직접 요청을 404로 차단하고, originals·backups는 마운트하지 않습니다. Compose 외부 포트는 Nginx의 80·443뿐입니다.

## 배포·롤백

새 버전을 개발 환경에서 테스트·이미지 빌드 → 현재 버전 백업 → 이미지 전달·load → `.env`의 `APP_IMAGE`를 새 태그로 변경 → `scripts/deploy.sh`. 마감 직전에는 배포하지 않습니다. 단일 서버이므로 중단이 발생할 수 있습니다. 스키마 변경은 이전 버전과의 호환성을 검토하세요.

앱만 되돌릴 때 `scripts/rollback.sh chembig:이전검증태그`를 실행한 뒤 `.env`에도 같은 태그를 저장합니다. DB를 자동으로 되돌리지 않습니다. 비호환 스키마는 아래의 격리 복원으로 검증 후 전환하세요.

## 채점 복구

PostgreSQL의 pending·processing 상태가 작업의 기준입니다. 파일·DB 저장을 완료하고 Redis에 등록하므로 브로커 장애에도 접수 레코드는 남습니다. cron의 reconcile이 매분 pending 작업을 재등록하며, 5분이 지난 processing 작업의 lease를 무효화하고 복구합니다. 작업 hard limit은 240초, 작업자는 하나씩 실행합니다. 중복 큐 메시지는 원자적 상태 획득과 lease 조건부 갱신으로 결과를 한 번만 확정합니다.

최초 실행 + 자동 재시도 3회 이후 error로 표시하고 한도에서 제외합니다. 운영 화면에서 원인을 해결한 뒤 재처리하세요. 서버 오류로 환급된 제출은 원본 접수의 복구로 처리하고 한도를 다시 차감하지 않습니다. 이 이력은 `quota_exempt`로 기록합니다. 재처리로 기존 날짜의 한도를 초과하는 성공 결과가 생길 수 있지만, 학생이 새로 제출할 수 있는 유효 접수 한도는 유지됩니다. 최종 확정은 미해결 서버 오류가 있으면 차단됩니다. 채점기 버전(`SCORER_VERSION`)을 올려 배포하면 그 시점에 대기 중이던 제출은 새 버전으로 채점되고 `scorer_version`에 그 버전이 기록됩니다. 접수 시점과 다른 버전으로 채점한 사실은 작업자 로그에 경고로 남습니다. 데이터 버전이 다른 제출만 서버 오류로 처리됩니다.

Redis를 재시작하거나 작업자를 재시작한 뒤 `docker compose exec web python manage.py reconcile`을 실행할 수 있습니다. 버전 불일치·파일 오류는 내부 로그에만 상세 기록하고 학생에게 경로·정답을 노출하지 않습니다. reconcile은 하루 넘게 지난 로그인 제한 카운터 행도 함께 정리합니다.

요청 처리 중 발생한 서버 오류(500)와 채점 실패의 상세 내용은 `docker compose logs web worker`에 그대로 출력됩니다. 학생 화면에는 일반 오류 페이지만 표시됩니다.

세부 지표 도입(`chembig-2`) 이전에 채점된 제출은 순위 점수만 있고 `세부 지표`가 비어 있습니다. `docker compose exec web python manage.py backfill_metrics --dry-run`으로 대상을 확인한 뒤 `--dry-run`을 빼고 실행하면 저장된 파일로 나머지 지표만 계산해 채웁니다. 저장된 순위 점수는 절대 바꾸지 않으며, 재계산 값이 저장 점수와 다르면 그 제출은 건너뛰고 표준 오류에 보고합니다.

## 백업과 별도 PC 사본

`scripts/backup.sh`는 DB의 일관된 pg_dump를 먼저 저장하고 불변 데이터·학생용 파일·제출을 압축합니다. 각각의 SHA-256과 이미지 정보를 manifest에 기록하고 완료 후에만 성공 시각을 갱신합니다. 최근 30일을 유지합니다. 같은 서버 백업은 서버 자체 손실을 막지 못합니다.

교수자 PC(SSH·rsync·Bash가 있는 macOS/Linux/WSL)에서 매일 실행합니다. 운영자가 백업 폴더를 읽을 수 있는 SSH 계정을 준비하세요. 개인키 권한은 600을 사용합니다.

```bash
CHEMBIG_REMOTE_SUDO=1 scripts/pull-backup.sh ubuntu@SERVER /opt/chembig/var/backups ./chembig-backups /absolute/path/to/key.pem
python3 scripts/verify-backup.py ./chembig-backups/백업시각
```

`CHEMBIG_REMOTE_SUDO=1`은 SSH 계정이 비밀번호 없이 sudo를 사용할 수 있을 때 root 소유 백업을 읽습니다. 계정이 직접 파일을 읽을 수 있으면 이 설정을 생략하세요.

다운로드는 원격 삭제를 로컬에 반영하지 않습니다. 교수자 PC에서 백업 실패 여부와 디스크 공간을 확인하세요. 원본·학번·성적이 포함되므로 PC 디스크 암호화와 접근 제한을 적용하세요.

복원은 실행 중인 DB에 덮어쓰지 않고 **새 DB와 존재하지 않는 파일 디렉터리**에 수행합니다.

```bash
scripts/restore.sh /absolute/backup/TIMESTAMP chembig_restore /absolute/path/restored-data
```

별도 환경에서 새 `.env`에 복원 DB와 DATA_DIR를 지정하고 파일 소유권을 UID 10001로 맞춥니다. 로그인·참가자·문제 파일·제출·점수·manifest 검사를 완료한 뒤 전환하세요. 기존 SECRET_KEY도 별도로 안전하게 보관해야 합니다. 백업에는 `.env`, 인증서와 소스가 포함되지 않습니다. 인증서는 도메인으로 다시 발급할 수 있으며 소스·검증 이미지는 별도로 보존합니다. 점수 산출 버전이 다른 앱으로 복원해 채점하지 마세요.

## 운영 확인

`/ops/`에서 백업 성공 시각, 남은 디스크, 채점 대기·실패를 확인합니다. `docker compose ps`, `docker compose logs --tail=100 web worker nginx`로 상태를 확인합니다. Docker 로그는 서비스별 10MB × 3개로 제한됩니다. 실제 서비스 주소에서 학생 계정 가입·승인·다운로드·제출·최종 공개를 모의 실행하고 서버 재부팅 후 데이터를 확인한 뒤 수업에 사용하세요.

참고: [Django 배포 점검](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/), [django-otp 관리자 인증](https://django-otp-official.readthedocs.io/en/stable/auth.html), [Celery 작업과 재시도](https://docs.celeryq.dev/en/latest/userguide/tasks.html), [Nginx internal](https://nginx.org/en/docs/http/ngx_http_core_module.html#internal).

## 현재 IP·8502 접속 구성

현재 서비스 주소는 `http://52.78.201.155:8502`입니다. 기존 DB와 파일을 유지하면서 Nginx를 `0.0.0.0:8502`에 연결했습니다. `config.http_preview` 설정으로 HTTP를 사용하되 DEBUG는 끕니다. 현재 연결 정보는 `artifacts/verify.env`, 실행 구성은 `compose.yaml:compose.verify.yaml:compose.public.yaml`, Compose 프로젝트는 `chembig-e2e`입니다.

재시작은 `scripts/serve-public.sh`를 사용합니다. 앱 변경 시 `docker build -t chembig:public-8502 .`로 이미지를 만든 뒤 실행합니다. 호스트 재부팅 시 Compose의 `unless-stopped` 정책으로 복구됩니다. 공개 서비스에는 검증용 `stack-check.py`와 `verify-stack-setup.sh`를 실행하지 않도록 포트 확인을 추가했습니다. HTTPS 도메인 배포 시에는 기본 TLS 운영 설정으로 전환하세요.

## 명령으로 대회 만들기 (TDC 벤치마크 등)

CSV 세 개와 JSON 스펙으로 대회·문제를 한 번에 만들고 게시할 수 있습니다. 스펙 형식은 `arena/management/commands/create_contest.py` 상단 주석을 참고하세요. 날짜는 ISO 8601이며 시간대가 없으면 서울 시간으로 해석합니다.

```bash
docker compose cp ./var/imports/bbbp web:/tmp/bbbp
docker compose exec web python manage.py create_contest /tmp/bbbp/spec.json --actor 관리자ID
```

기존 대회에 문제만 추가하려면 `--contest-id`를 사용합니다. 같은 학기에 같은 제목의 대회가 있으면 중단합니다. 스펙의 `publish: true`는 데이터 검사 후 즉시 게시하므로, 먼저 `false`로 만들어 `/ops/problems/<id>/data/`에서 미리보기를 확인하는 방법도 있습니다.

TDC ADMET 벤치마크 과제는 `scripts/fetch_tdc.py`로 변환합니다. PyTDC는 플랫폼 의존성이 아니므로 별도 가상환경에서 실행합니다 (설치 명령은 스크립트 상단 주석). 예: `python scripts/fetch_tdc.py BBB_Martins --out var/imports/bbbp` 는 TDC가 고정한 scaffold-split test 집합과 seed 1 train/valid 분할을 `train.csv`·`val.csv`·`test.csv`와 `spec.json` 초안으로 저장합니다. 행 순서는 섞고 ID는 `train_0001` 형식으로 다시 부여하므로 정답 정보가 새지 않습니다. 초대 코드·기간·설명을 수정한 뒤 `create_contest`로 등록하세요.

현재 8502 서비스에는 2026년 2학기 `TDC ADMET 벤치마크 · BBBP 혈액뇌장벽 투과 예측` 대회(마감 2026-12-04 23:59:59 KST, 순위 지표 ROC-AUC)가 이 절차로 등록되어 있습니다. 초대 코드는 `/admin/arena/contest/`에서 확인합니다.
