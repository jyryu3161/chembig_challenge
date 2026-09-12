import csv
import io
import json
import secrets
import shutil
import uuid
from functools import wraps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import Contest, Problem, Membership, Submission, FinalChoice, Announcement, User, Audit
from .forms import SignupForm, ProfileForm, JoinForm, UploadForm, DatasetForm, RecoveryForm, CloneForm
from . import services


def staff_only(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not (request.user.is_active and request.user.is_staff and request.user.is_verified()):
            return redirect('/admin/login/?next=' + request.path)
        return view(request, *args, **kwargs)
    return wrapped


def is_operator(user):
    return user.is_authenticated and user.is_active and user.is_staff and user.is_verified()


def access(request, contest):
    if is_operator(request.user): return
    if not request.user.is_authenticated or not Membership.objects.filter(user=request.user, contest=contest, status='approved').exists():
        raise PermissionDenied('참가 승인 후 이용할 수 있습니다.')


def home(request):
    now = timezone.now()
    contests = Contest.objects.filter(visible=True)
    return render(request, 'arena/home.html', {'contests':contests.filter(closes_at__gt=now)[:3],
        'active_count':contests.filter(opens_at__lte=now, closes_at__gt=now).count(),
        'archive_count':contests.filter(closes_at__lte=now).count(),
        'announcements':Announcement.objects.filter(Q(contest__isnull=True)|Q(contest__visible=True))[:4]})


def contests(request):
    archived = request.GET.get('archive') == '1'
    qs = Contest.objects.filter(visible=True)
    qs = qs.filter(closes_at__lte=timezone.now()) if archived else qs.filter(closes_at__gt=timezone.now())
    return render(request, 'arena/contests.html', {'contests':qs, 'archived':archived})


def guide(request): return render(request, 'arena/guide.html')


def signup(request):
    form = SignupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect('contests')
    return render(request, 'arena/form.html', {'form':form, 'title':'회원가입', 'intro':'이메일 없이 가입하고, 수업 초대 코드로 참가를 신청하세요.', 'button':'계정 만들기'})


@login_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, '공개 이름 설정을 저장했습니다.')
        return redirect('profile')
    return render(request, 'arena/form.html', {'form':form, 'title':'내 프로필', 'intro':'순위표에는 기본으로 별명이 표시됩니다. 이름·학번은 관리자만 확인합니다.', 'button':'설정 저장'})


def recover(request):
    form = RecoveryForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try: services.redeem_recovery(form.cleaned_data['username'], form.cleaned_data['code'], form.cleaned_data['password'])
        except ValidationError as e: form.add_error(None, e)
        else:
            messages.success(request, '비밀번호를 변경하고 기존 로그인 세션을 해제했습니다.')
            return redirect('login')
    return render(request, 'arena/form.html', {'form':form, 'title':'계정 복구', 'intro':'교수자에게 본인 확인을 받은 후 30분 동안 유효한 복구 코드를 전달받으세요.', 'button':'비밀번호 재설정'})


def contest(request, pk, tab='overview'):
    c = get_object_or_404(Contest, pk=pk, visible=True)
    if tab not in ['overview','problems','rules','leaderboard','submit','submissions']: raise Http404
    member = Membership.objects.filter(user=request.user, contest=c).first() if request.user.is_authenticated else None
    allowed = is_operator(request.user) or bool(member and member.status == 'approved')
    if tab in ['problems','leaderboard','submit','submissions']: access(request,c)
    problems = c.problems.filter(published_at__isnull=False)
    selected = problems.filter(pk=request.GET.get('problem')).first() if request.GET.get('problem','').isdigit() else problems.first()
    rows = []
    if selected and tab == 'leaderboard':
        qs = Submission.objects.filter(problem=selected, status='scored', user__membership__contest=c, user__membership__status='approved').select_related('user')
        if c.released_at: qs = qs.filter(finalchoice__problem=selected)
        seen = set()
        for s in services.ordered_scores(selected, qs, final=bool(c.released_at)):
            if s.user_id not in seen:
                seen.add(s.user_id)
                rows.append({'rank':len(rows)+1, 'name':s.user.public_name, 'mine':s.user_id==request.user.pk,
                    'score':s.test_score if c.released_at else s.val_score, 'received_at':s.received_at})
    submissions = []
    if tab == 'submissions':
        choices = set(FinalChoice.objects.filter(user=request.user, problem__contest=c).values_list('submission_id', flat=True))
        for s in Submission.objects.filter(user=request.user, problem__contest=c).select_related('problem').order_by('-received_at')[:200]:
            submissions.append({'id':s.pk, 'problem':s.problem, 'received_at':s.received_at, 'status':s.get_status_display(),
                'val_score':s.val_score, 'test_score':s.test_score if c.released_at else None, 'selected':s.pk in choices,
                'can_select':c.is_open and s.status=='scored'})
    return render(request, 'arena/contest.html', {'contest':c,'tab':tab,'membership':member,'allowed':allowed,'problems':problems,
        'selected_problem':selected,'rows':rows,'submissions':submissions,'join_form':JoinForm(),
        'upload_form':UploadForm(initial={'request_key':uuid.uuid4()}),
        'announcements':Announcement.objects.filter(contest=c)[:10]})


@login_required
@require_POST
def join(request, pk):
    c = get_object_or_404(Contest, pk=pk, visible=True)
    form = JoinForm(request.POST)
    if not c.ended and form.is_valid() and secrets.compare_digest(form.cleaned_data['invite_code'], c.invite_code):
        try:
            with transaction.atomic():
                Membership.objects.get_or_create(user=request.user, contest=c, defaults={'student_id':request.user.student_id})
            messages.success(request, '참가 신청을 접수했습니다. 관리자 승인을 기다려 주세요.')
        except IntegrityError: messages.error(request, '이 학번은 이미 대회에 참가 신청되어 있습니다. 관리자에게 문의하세요.')
    else: messages.error(request, '초대 코드 또는 참가 신청 기간을 확인하세요.')
    return redirect('contest', pk=pk)


@login_required
@require_POST
def submit(request, pk):
    p = get_object_or_404(Problem, pk=pk, published_at__isnull=False, contest__visible=True)
    access(request,p.contest)
    form = UploadForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            raw = services.read_upload(form.cleaned_data['file'])
            received_at = timezone.now() # entire file has now reached the application
            services.accept_submission(request.user, p, raw, form.cleaned_data['request_key'], received_at)
        except ValidationError as e: messages.error(request, ' '.join(e.messages))
        else:
            messages.success(request, 'CSV를 접수했습니다. 채점 결과는 내 제출에서 확인하세요.')
            return redirect('contest-tab', pk=p.contest_id, tab='submissions')
    else: messages.error(request, '파일과 제출 요청을 확인하세요. 제출 화면을 새로 열어 다시 시도하세요.')
    return redirect('contest-tab', pk=p.contest_id, tab='submit')


@login_required
@require_POST
def choose(request, sid):
    s = get_object_or_404(Submission, pk=sid, user=request.user)
    try: services.choose_final(request.user, sid)
    except ValidationError as e: messages.error(request,' '.join(e.messages))
    else: messages.success(request,'최종 제출로 지정했습니다.')
    return redirect('contest-tab', pk=s.problem.contest_id, tab='submissions')


def send_file(relative, name, prefix):
    if not relative.startswith(prefix+'/'): raise Http404
    path = services.disk_path(relative)
    if not path.is_file(): raise Http404
    if settings.USE_X_ACCEL:
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['X-Accel-Redirect'] = '/_protected_' + prefix + '/' + relative.removeprefix(prefix+'/')
        response['Content-Disposition'] = f'attachment; filename="{name}"'
    else: response = FileResponse(path.open('rb'), as_attachment=True, filename=name, content_type='text/csv')
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def download(request, pk, split):
    p = get_object_or_404(Problem, pk=pk, published_at__isnull=False, contest__visible=True)
    access(request,p.contest)
    if split not in ['train','val','test','sample']: raise Http404
    return send_file(p.manifest[split]['student'], 'sample_submission.csv' if split=='sample' else split+'.csv', 'student')


@login_required
def submission_file(request, sid):
    s = get_object_or_404(Submission, pk=sid)
    if not is_operator(request.user) and s.user_id != request.user.pk: raise PermissionDenied
    return send_file(s.path, f'{s.pk}.csv', 'submissions')


@staff_only
def operations(request):
    root = settings.DATA_ROOT
    root.mkdir(parents=True,exist_ok=True)
    usage = shutil.disk_usage(root)
    backup = None
    try: backup = json.loads((root/'backups'/'last_success.json').read_text())
    except (OSError, ValueError): pass
    return render(request, 'arena/operations.html', {'contests':Contest.objects.all(),
        'members':Membership.objects.select_related('user','contest').order_by('status','created_at'),
        'failed':Submission.objects.filter(status='error').select_related('user','problem'),
        'pending':Submission.objects.filter(status__in=['pending','processing']).count(),
        'free_gb':round(usage.free/1024**3,1), 'backup':backup, 'audits':Audit.objects.select_related('actor')[:15]})


@staff_only
@require_POST
def approve(request):
    state = request.POST.get('state')
    if state not in ['approved','suspended']: raise Http404
    with transaction.atomic():
        qs = Membership.objects.select_for_update().filter(pk__in=request.POST.getlist('members'), contest__closes_at__gt=timezone.now())
        for m in qs:
            m.status=state
            m.save(update_fields=['status'])
            services.audit(request.user,'참가 '+state,f'{m.contest_id}/{m.user_id}')
    messages.success(request,'선택한 참가자의 상태를 변경했습니다. 종료된 대회는 제외됩니다.')
    return redirect('operations')


@staff_only
def dataset(request, pk):
    p = get_object_or_404(Problem, pk=pk)
    form = DatasetForm(request.POST or None, request.FILES or None)
    if request.method == 'POST':
        try:
            if request.POST.get('action') == 'publish':
                services.publish_problem(pk, request.user)
                messages.success(request,'문제를 게시했습니다. 데이터와 평가 설정이 고정되었습니다.')
                return redirect('dataset',pk=pk)
            if form.is_valid():
                services.prepare_data(pk,{s:services.read_upload(form.cleaned_data[s]) for s in ['train','val','test']},request.user)
                return redirect('dataset',pk=pk)
        except ValidationError as e: form.add_error(None,e)
    previews = []
    for split in ['train','val','test','sample']:
        if split in p.manifest:
            header, rows = services.read_csv(services.disk_path(p.manifest[split]['student']).read_bytes())
            previews.append({'split':split,'count':p.manifest[split]['rows'],'header':header,'rows':[[r[h] for h in header] for r in rows[:5]]})
    return render(request,'arena/dataset.html',{'problem':p,'form':form,'previews':previews})


@staff_only
@require_POST
def contest_action(request, pk, action):
    try:
        if action=='finalize': services.finalize(pk,request.user)
        elif action=='release': services.release(pk,request.user)
        else: raise Http404
    except ValidationError as e: messages.error(request,' '.join(e.messages))
    else: messages.success(request,'처리를 완료했습니다.')
    return redirect('operations')


@staff_only
@require_POST
def retry(request, sid):
    try: services.retry_submission(sid,request.user)
    except ValidationError as e: messages.error(request,' '.join(e.messages))
    return redirect('operations')


@staff_only
def recovery_issue(request):
    code = None
    if request.method=='POST':
        user = get_object_or_404(User, username=request.POST.get('username'))
        try: code=services.issue_recovery(user,request.user)
        except ValidationError as e: messages.error(request,' '.join(e.messages))
    response=render(request,'arena/recovery_issue.html',{'code':code})
    response['Cache-Control']='no-store'
    return response


@staff_only
def clone(request,pk):
    c=get_object_or_404(Contest,pk=pk)
    form=CloneForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        new=services.clone_contest(pk,form.cleaned_data['semester'],request.user)
        return redirect(f'/admin/arena/contest/{new.pk}/change/')
    return render(request,'arena/form.html',{'form':form,'title':'다음 학기로 복제','intro':f'{c.title}의 설명·규칙·문제 설정만 복제합니다. 새 데이터와 참가자로 시작합니다.','button':'새 대회 만들기'})


@staff_only
def export(request,pk):
    c=get_object_or_404(Contest,pk=pk)
    out=io.StringIO(newline='')
    writer=csv.writer(out)
    writer.writerow(['대회','문제','이름','학번','별명','순위','점수','평가','제출 횟수'])
    def safe(v):
        s=str(v)
        return "'"+s if s.startswith(('=','+','-','@','\t','\r','\n')) else s
    for p in c.problems.all():
        qs=Submission.objects.filter(problem=p,status='scored',user__membership__contest=c,user__membership__status='approved').select_related('user')
        if c.released_at: qs=qs.filter(finalchoice__problem=p)
        best={}
        for item in services.ordered_scores(p,qs,final=bool(c.released_at)):
            if item.user_id not in best: best[item.user_id]=(len(best)+1,item)
        for member in Membership.objects.filter(contest=c,status='approved').select_related('user').order_by('student_id'):
            rank,item=best.get(member.user_id,('',None))
            value=(item.test_score if c.released_at else item.val_score) if item else ''
            writer.writerow([safe(c.title),safe(p.title),safe(member.user.real_name),safe(member.student_id),safe(member.user.nickname),rank,
                value,'test' if c.released_at else 'val',Submission.objects.filter(user=member.user,problem=p).count()])
    services.audit(request.user,'성적 CSV 내보내기',pk)
    response=HttpResponse('\ufeff'+out.getvalue(),content_type='text/csv; charset=utf-8')
    response['Content-Disposition']=f'attachment; filename="contest-{pk}-grades.csv"'
    response['Cache-Control']='no-store'
    return response


def health(request):
    from django.db import connection
    with connection.cursor() as cursor: cursor.execute('SELECT 1')
    return JsonResponse({'status':'ok'})
