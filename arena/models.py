import uuid
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from . import scoring

class User(AbstractUser):
    real_name = models.CharField('이름', max_length=60)
    student_id = models.CharField('학번', max_length=30)
    nickname = models.CharField('별명', max_length=40)
    show_real_name = models.BooleanField('실명 표시', default=False)

    @property
    def public_name(self):
        return self.real_name if self.show_real_name else self.nickname

class Semester(models.Model):
    year = models.PositiveIntegerField('학년도')
    term = models.CharField('학기', max_length=20)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['year', 'term'], name='semester_unique')]
        ordering = ['-year', '-term']
        verbose_name = '학년도·학기'
        verbose_name_plural = verbose_name
    def __str__(self):
        return f'{self.year} · {self.term}'

class Contest(models.Model):
    semester = models.ForeignKey(Semester, on_delete=models.PROTECT)
    title = models.CharField('대회명', max_length=180)
    description = models.TextField('소개')
    rules = models.TextField('규칙', default='개인전 · 외부 데이터 사용 여부는 교수자의 안내를 따릅니다.')
    opens_at = models.DateTimeField('시작 시각')
    closes_at = models.DateTimeField('마감 시각')
    daily_limit = models.PositiveSmallIntegerField('문제별 일일 제출 제한', default=5)
    invite_code = models.CharField('초대 코드', max_length=80)
    visible = models.BooleanField('대회 표시', default=False)
    finalized_at = models.DateTimeField(null=True, blank=True, editable=False)
    released_at = models.DateTimeField(null=True, blank=True, editable=False)
    class Meta:
        ordering = ['-opens_at']
        verbose_name = '대회'
        verbose_name_plural = verbose_name
    def __str__(self):
        return self.title
    def clean(self):
        # full_clean() still calls clean() after field errors, so blank dates arrive here as None.
        if self.opens_at and self.closes_at and self.opens_at >= self.closes_at:
            raise ValidationError('종료는 시작 이후여야 합니다.')
        if self.daily_limit is not None and self.daily_limit < 1:
            raise ValidationError('제출 제한은 1 이상이어야 합니다.')
        if self.pk:
            old = Contest.objects.get(pk=self.pk)
            if old.finalized_at or old.closes_at <= timezone.now():
                fields = ['semester_id', 'title', 'description', 'rules', 'opens_at', 'closes_at', 'daily_limit', 'invite_code', 'visible']
                if any(getattr(old, f) != getattr(self, f) for f in fields):
                    raise ValidationError('종료된 대회는 읽기 전용입니다. 새 학기로 복제하세요.')
    @property
    def is_open(self):
        return self.visible and self.opens_at <= timezone.now() < self.closes_at and not self.finalized_at
    @property
    def ended(self):
        return timezone.now() >= self.closes_at
    @property
    def phase(self):
        if self.released_at: return '최종 결과 공개'
        if self.ended: return '종료 · 결과 준비'
        if self.is_open: return '진행 중'
        return '예정'

class Membership(models.Model):
    STATUS = [('pending', '승인 대기'), ('approved', '참가 승인'), ('suspended', '참가 정지')]
    user = models.ForeignKey(User, on_delete=models.PROTECT)
    contest = models.ForeignKey(Contest, on_delete=models.PROTECT)
    student_id = models.CharField('학번', max_length=30)
    status = models.CharField('상태', max_length=12, choices=STATUS, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'contest'], name='one_membership'),
            models.UniqueConstraint(fields=['contest', 'student_id'], name='one_student_per_contest')]
        verbose_name = '참가 신청'
        verbose_name_plural = verbose_name

class Problem(models.Model):
    METRICS = scoring.METRIC_CHOICES
    contest = models.ForeignKey(Contest, on_delete=models.PROTECT, related_name='problems')
    title = models.CharField('문제명', max_length=160)
    kind = models.CharField('유형', max_length=16, choices=scoring.KINDS)
    metric = models.CharField('순위 지표', max_length=24, choices=METRICS, default='rmse',
        help_text='순위를 결정하는 주 지표입니다. 같은 유형의 나머지 지표는 참고용으로 함께 계산됩니다.')
    description = models.TextField('예측 대상·설명')
    units = models.CharField('단위·변환', max_length=200)
    source = models.TextField('데이터 출처')
    split_method = models.TextField('분할 방법')
    id_column = models.CharField('ID 열', max_length=80, default='sample_id')
    smiles_column = models.CharField('SMILES 열', max_length=80, default='SMILES')
    target_column = models.CharField('정답 열', max_length=80, default='target')
    feature_columns = models.JSONField('공개 특징 열 (JSON 배열)', default=list, blank=True)
    dataset_version = models.CharField(max_length=64, blank=True, editable=False)
    manifest = models.JSONField(default=dict, editable=False)
    warnings = models.JSONField(default=list, editable=False)
    published_at = models.DateTimeField(null=True, blank=True, editable=False)
    class Meta:
        verbose_name = '문제'
        verbose_name_plural = verbose_name
        ordering = ['pk']
    def __str__(self): return self.title
    @property
    def minimize(self): return scoring.REGISTRY[self.metric].minimize
    @property
    def metric_label(self): return scoring.REGISTRY[self.metric].label
    @property
    def all_metrics(self):
        """Every metric computed for this problem's kind, primary first."""
        return [scoring.REGISTRY[self.metric], *[m for m in scoring.metrics_for(self.kind) if m.key != self.metric]]
    @property
    def secondary_metrics(self): return self.all_metrics[1:]
    def clean(self):
        if self.contest_id and self.contest.ended and not self.published_at:
            raise ValidationError('종료된 대회에 문제를 추가하거나 수정할 수 없습니다.')
        if self.metric not in scoring.REGISTRY or scoring.metric_kind(self.metric) != self.kind:
            allowed = ', '.join(m.label for m in scoring.metrics_for(self.kind))
            raise ValidationError(f'{self.get_kind_display()} 문제의 순위 지표는 {allowed} 중에서 선택하세요.')
        if not isinstance(self.feature_columns, list) or not all(isinstance(x, str) for x in self.feature_columns):
            raise ValidationError('공개 특징 열은 문자열의 JSON 배열이어야 합니다.')
        cols = [self.id_column, self.smiles_column, self.target_column, *self.feature_columns]
        if len(set(cols)) != len(cols):
            raise ValidationError('ID·SMILES·정답·특징 열은 중복될 수 없습니다.')
        if self.pk and Problem.objects.get(pk=self.pk).published_at:
            old = Problem.objects.get(pk=self.pk)
            fields = ['contest_id','title','kind','metric','description','units','source','split_method',
                      'id_column','smiles_column','target_column','feature_columns']
            if any(getattr(old, f) != getattr(self, f) for f in fields):
                raise ValidationError('게시된 문제의 데이터·설정은 고정됩니다. 새 문제를 만드세요.')

class Submission(models.Model):
    STATUS = [('pending', '채점 대기'), ('processing', '채점 중'), ('scored', '채점 완료'), ('error', '서버 오류')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.PROTECT)
    problem = models.ForeignKey(Problem, on_delete=models.PROTECT)
    request_key = models.UUIDField()
    received_at = models.DateTimeField()
    quota_day = models.DateField()
    path = models.CharField(max_length=220)
    file_hash = models.CharField(max_length=64)
    dataset_version = models.CharField(max_length=64)
    scorer_version = models.CharField(max_length=50)
    status = models.CharField(max_length=16, choices=STATUS, default='pending')
    val_score = models.FloatField(null=True, blank=True)
    test_score = models.FloatField(null=True, blank=True)
    # Every metric of the problem kind, keyed by metric key; the primary metric is duplicated in *_score for ranking.
    val_metrics = models.JSONField(default=dict, blank=True)
    test_metrics = models.JSONField(default=dict, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    quota_exempt = models.BooleanField(default=False, editable=False)
    lease = models.UUIDField(null=True)
    started_at = models.DateTimeField(null=True)
    error = models.CharField(max_length=250, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'problem', 'request_key'], name='idempotent_submission')]
        indexes = [models.Index(fields=['status','started_at']), models.Index(fields=['user','problem','quota_day'])]
        ordering = ['received_at', 'id']
        verbose_name = '제출'
        verbose_name_plural = verbose_name

class FinalChoice(models.Model):
    user = models.ForeignKey(User, on_delete=models.PROTECT)
    problem = models.ForeignKey(Problem, on_delete=models.PROTECT)
    submission = models.ForeignKey(Submission, on_delete=models.PROTECT)
    automatic = models.BooleanField(default=False)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user','problem'], name='one_final_choice')]

class RecoveryCode(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    digest = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True)

class Audit(models.Model):
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=120)
    detail = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']
        verbose_name = '작업 기록'
        verbose_name_plural = verbose_name

class Announcement(models.Model):
    contest = models.ForeignKey(Contest, on_delete=models.PROTECT, null=True, blank=True)
    title = models.CharField('제목', max_length=180)
    body = models.TextField('내용')
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']
        verbose_name = '공지사항'
        verbose_name_plural = verbose_name

class AuthAttempt(models.Model):
    key = models.CharField(max_length=64, unique=True)
    count = models.PositiveIntegerField(default=0)
    window_start = models.DateTimeField(default=timezone.now)
