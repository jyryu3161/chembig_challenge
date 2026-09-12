import hashlib
import secrets
import uuid
from datetime import timedelta
from pathlib import Path
from django.conf import settings
from django.contrib.auth.hashers import make_password, check_password
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone
from .models import (Problem, Contest, Membership, Submission, FinalChoice, RecoveryCode, User, Audit)
from .scoring import validate_datasets, validate_predictions, csv_bytes, read_csv, score


def audit(actor, action, detail):
    Audit.objects.create(actor=actor, action=action, detail=str(detail))


def disk_path(relative):
    root = settings.DATA_ROOT.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root): raise PermissionDenied
    return path


def write_file(relative, data):
    path = disk_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as f: f.write(data)


def read_upload(upload):
    if upload.size > settings.MAX_CSV_BYTES:
        raise ValidationError('CSV 파일은 최대 10MB까지 업로드할 수 있습니다.')
    raw = upload.read(settings.MAX_CSV_BYTES + 1)
    if len(raw) > settings.MAX_CSV_BYTES:
        raise ValidationError('CSV 파일은 최대 10MB까지 업로드할 수 있습니다.')
    return raw


@transaction.atomic
def prepare_data(problem_id, files, actor):
    p = Problem.objects.select_for_update().get(pk=problem_id)
    if p.published_at or p.contest.ended: raise ValidationError('게시된 문제 또는 종료된 대회의 데이터는 변경할 수 없습니다.')
    p.full_clean()
    datasets, warnings = validate_datasets(p, files)
    version = hashlib.sha256(b''.join(files[s] for s in ('train','val','test'))).hexdigest()
    folder = f'{p.pk}/{uuid.uuid4().hex}'
    manifest = {}
    for split, rows in datasets.items():
        original = f'originals/{folder}/{split}.csv'
        student = f'student/{folder}/{split}.csv'
        columns = [p.id_column, p.smiles_column, *p.feature_columns]
        if split == 'train': columns.append(p.target_column)
        # Student ID column is always sample_id, matching the submission contract.
        public_rows = [{('sample_id' if k == p.id_column else k): v for k, v in r.items() if k in columns} for r in rows]
        public_columns = ['sample_id' if k == p.id_column else k for k in columns]
        if len(set(public_columns)) != len(public_columns):
            raise ValidationError('공개 열 이름 sample_id는 ID 열에만 사용할 수 있습니다.')
        write_file(original, files[split])
        write_file(student, csv_bytes(public_columns, public_rows))
        manifest[split] = {'original': original, 'student': student, 'rows': len(rows)}
    sample = f'student/{folder}/sample_submission.csv'
    write_file(sample, csv_bytes(['sample_id','prediction'],
        [{'sample_id': r[p.id_column], 'prediction': 0.5 if p.kind == 'binary' else 0} for split in ('val','test') for r in datasets[split]]))
    manifest['sample'] = {'student': sample, 'rows': len(datasets['val'])+len(datasets['test'])}
    p.manifest, p.warnings, p.dataset_version = manifest, warnings, version
    p.save(update_fields=['manifest','warnings','dataset_version'])
    audit(actor, '데이터 검사·학생용 파일 생성', p.pk)
    return p


@transaction.atomic
def publish_problem(problem_id, actor):
    p = Problem.objects.select_for_update().get(pk=problem_id)
    if p.contest.ended or not p.manifest: raise ValidationError('기간과 데이터 검사를 확인하세요.')
    if not p.published_at:
        p.full_clean()
        # Revalidate against current column/metric settings after an admin edit.
        files = {s: disk_path(p.manifest[s]['original']).read_bytes() for s in ('train','val','test')}
        p = prepare_data(p.pk, files, actor)
        p.published_at = timezone.now()
        p.save(update_fields=['published_at'])
        audit(actor, '문제 게시·평가 조건 고정', p.pk)
    return p


def expected_ids(p):
    return [r['sample_id'] for split in ('val','test') for r in read_csv(disk_path(p.manifest[split]['student']).read_bytes())[1]]


def evaluate(p, raw, split):
    predictions = validate_predictions(p, raw, expected_ids(p))
    rows = read_csv(disk_path(p.manifest[split]['original']).read_bytes())[1]
    return score(p.metric, [float(r[p.target_column]) for r in rows], [predictions[r[p.id_column]] for r in rows])


@transaction.atomic
def accept_submission(user, problem, raw, request_key, received_at):
    # The contest lock serializes cutoff/finalization, the membership lock reserves quota.
    contest = Contest.objects.select_for_update().get(pk=problem.contest_id)
    membership = Membership.objects.select_for_update().filter(user=user, contest=contest, status='approved').first()
    if not membership: raise PermissionDenied
    existing = Submission.objects.filter(user=user, problem=problem, request_key=request_key).first()
    if existing:
        if existing.file_hash != hashlib.sha256(raw).hexdigest():
            raise ValidationError('같은 요청 키에 다른 파일을 보낼 수 없습니다. 제출 화면을 새로 여세요.')
        return existing
    if not problem.published_at or not contest.visible or contest.finalized_at or not contest.opens_at <= received_at < contest.closes_at:
        raise ValidationError('제출 기간이 아닙니다. 마감은 파일 전체의 서버 접수 완료 시각을 기준으로 합니다.')
    if len(raw) > settings.MAX_CSV_BYTES: raise ValidationError('CSV 파일은 최대 10MB입니다.')
    validate_predictions(problem, raw, expected_ids(problem))
    # Reject numerical overflow as an invalid submission without consuming quota.
    if problem.minimize:
        try: evaluate(problem, raw, 'val'); evaluate(problem, raw, 'test')
        except (FloatingPointError, OverflowError, ValueError): raise ValidationError('예측값의 수치 범위가 너무 큽니다.')
    day = timezone.localtime(received_at).date()
    count = Submission.objects.filter(user=user, problem=problem, quota_day=day, quota_exempt=False).exclude(status='error').count()
    if count >= contest.daily_limit: raise ValidationError(f'오늘의 유효 제출 {contest.daily_limit}회를 모두 사용했습니다. 채점 대기도 횟수에 포함됩니다.')
    sid = uuid.uuid4()
    path = f'submissions/{user.pk}/{sid}.csv'
    write_file(path, raw)
    s = Submission.objects.create(id=sid, user=user, problem=problem, request_key=request_key, received_at=received_at,
        quota_day=day, path=path, file_hash=hashlib.sha256(raw).hexdigest(), dataset_version=problem.dataset_version,
        scorer_version=settings.SCORER_VERSION)
    transaction.on_commit(lambda: dispatch(s.pk))
    return s


def dispatch(sid):
    import logging
    from .tasks import grade
    try: grade.apply_async(args=[str(sid)], retry=False)
    except Exception:
        logging.getLogger(__name__).warning('Broker unavailable; submission remains in PostgreSQL for reconciliation.')


def ordered_scores(problem, queryset, final=False):
    field = 'test_score' if final else 'val_score'
    return queryset.order_by(field if problem.minimize else '-'+field, 'received_at', 'id')


@transaction.atomic
def choose_final(user, submission_id):
    s = Submission.objects.select_related('problem').get(pk=submission_id, user=user)
    c = Contest.objects.select_for_update().get(pk=s.problem.contest_id)
    if not c.is_open or s.status != 'scored': raise ValidationError('마감 전 채점 완료 제출만 지정할 수 있습니다.')
    if not Membership.objects.filter(user=user, contest=c, status='approved').exists(): raise PermissionDenied
    FinalChoice.objects.update_or_create(user=user, problem=s.problem, defaults={'submission':s, 'automatic':False})


@transaction.atomic
def finalize(contest_id, actor=None):
    c = Contest.objects.select_for_update().get(pk=contest_id)
    if c.finalized_at: return c
    if not c.ended: raise ValidationError('마감 후 확정할 수 있습니다.')
    if Submission.objects.filter(problem__contest=c, status__in=['pending','processing']).exists():
        raise ValidationError('마감 전에 접수된 채점 작업이 남아 있습니다.')
    if Submission.objects.filter(problem__contest=c, status='error').exists():
        raise ValidationError('서버 오류 제출을 재처리한 뒤 확정하세요.')
    for p in c.problems.filter(published_at__isnull=False):
        submissions = Submission.objects.filter(problem=p, status='scored', received_at__lt=c.closes_at)
        for uid in submissions.values_list('user_id', flat=True).distinct():
            choice = FinalChoice.objects.filter(user_id=uid, problem=p).first()
            if not choice:
                s = ordered_scores(p, submissions.filter(user_id=uid)).first()
                choice = FinalChoice.objects.create(user_id=uid, problem=p, submission=s, automatic=True)
            s = choice.submission
            s.test_score = evaluate(p, disk_path(s.path).read_bytes(), 'test')
            s.save(update_fields=['test_score'])
    c.finalized_at = timezone.now()
    c.save(update_fields=['finalized_at'])
    audit(actor, '최종 제출 확정·test 채점', c.pk)
    return c


@transaction.atomic
def release(contest_id, actor):
    c = Contest.objects.select_for_update().get(pk=contest_id)
    if not c.finalized_at: raise ValidationError('최종 채점을 먼저 확정하세요.')
    if not c.released_at:
        c.released_at = timezone.now()
        c.save(update_fields=['released_at'])
        audit(actor, '최종 결과 공개', c.pk)


@transaction.atomic
def retry_submission(sid, actor):
    s = Submission.objects.select_related('problem').get(pk=sid)
    c = Contest.objects.select_for_update().get(pk=s.problem.contest_id)
    membership = Membership.objects.select_for_update().get(user=s.user, contest=c)
    s = Submission.objects.select_for_update().get(pk=sid)
    if c.finalized_at or s.status != 'error': raise ValidationError('재처리할 서버 오류 제출이 아닙니다.')
    # A terminal infrastructure failure has already refunded its original quota slot.
    # Repairing that same receipt must not block finalization or charge a new slot.
    s.quota_exempt = True
    s.status, s.attempts, s.error = 'pending', 0, ''
    s.save(update_fields=['status','attempts','error','quota_exempt'])
    audit(actor, '채점 재처리', sid)
    transaction.on_commit(lambda: dispatch(s.pk))


@transaction.atomic
def clone_contest(contest_id, semester, actor):
    old = Contest.objects.get(pk=contest_id)
    new = Contest.objects.create(semester=semester, title=old.title, description=old.description, rules=old.rules,
        opens_at=timezone.now()+timedelta(days=7), closes_at=timezone.now()+timedelta(days=37),
        daily_limit=old.daily_limit, invite_code=secrets.token_urlsafe(12))
    for p in old.problems.all():
        p.pk = None
        p.contest, p.manifest, p.warnings, p.dataset_version, p.published_at = new, {}, [], '', None
        p.save()
    audit(actor, '학기 복제 (데이터·참가자·제출 제외)', f'{old.pk} → {new.pk}')
    return new


@transaction.atomic
def issue_recovery(user, actor):
    user = User.objects.select_for_update().get(pk=user.pk)
    if user.is_staff: raise ValidationError('관리자 복구는 서버의 reset_admin_otp 절차를 사용하세요.')
    RecoveryCode.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
    code = secrets.token_urlsafe(24)
    RecoveryCode.objects.create(user=user, digest=make_password(code), expires_at=timezone.now()+timedelta(minutes=30))
    audit(actor, '계정 복구 코드 발급', user.pk)
    return code


@transaction.atomic
def redeem_recovery(username, code, password):
    from django.contrib.auth.password_validation import validate_password
    user = User.objects.select_for_update().filter(username=username, is_staff=False, is_active=True).first()
    if not user: raise ValidationError('복구 코드가 올바르지 않거나 만료되었습니다.')
    recovery = RecoveryCode.objects.filter(user=user, used_at__isnull=True, expires_at__gt=timezone.now()).order_by('-pk').first()
    if not recovery or not check_password(code, recovery.digest):
        raise ValidationError('복구 코드가 올바르지 않거나 만료되었습니다.')
    validate_password(password, user)
    user.set_password(password)
    user.save(update_fields=['password'])
    recovery.used_at = timezone.now()
    recovery.save(update_fields=['used_at'])
    for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
        if str(session.get_decoded().get('_auth_user_id')) == str(user.pk): session.delete()
    audit(user, '계정 복구 완료·세션 해제', user.pk)
