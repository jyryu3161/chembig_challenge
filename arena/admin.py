from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import ValidationError
from django.db.models.functions import Substr
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django_otp.admin import OTPAdminSite
from .models import User, Semester, Contest, Membership, Problem, Submission, Audit, Announcement
from . import services

admin.site.__class__ = OTPAdminSite
admin.site.site_header = 'ChemBIG 관리자 · OTP 인증'
admin.site.site_title = 'ChemBIG 운영'
admin.site.index_title = '대회 설정'

class AuditedAdmin(admin.ModelAdmin):
    def save_model(self, request, obj, form, change):
        super().save_model(request,obj,form,change)
        Audit.objects.create(actor=request.user,action='관리자 설정 저장',detail=f'{obj._meta.label}/{obj.pk}')
    def has_delete_permission(self,request,obj=None): return False

@admin.register(User)
class StudentAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (('학생 정보',{'fields':('real_name','student_id','nickname','show_real_name')}),)
    add_fieldsets = UserAdmin.add_fieldsets + (('학생 정보',{'fields':('real_name','student_id','nickname')}),)
    list_display = ['username','real_name','student_id','nickname','is_staff']
    search_fields = ['username','real_name','student_id']
    def has_delete_permission(self,request,obj=None): return False

class ServiceDeleteMixin:
    """Deletion runs through arena.services (no-submission rule, data-file cleanup, audit) instead of the ORM
    collector, which would refuse every PROTECT relation. Only the per-object button exists; no bulk action."""
    def has_delete_permission(self,request,obj=None):
        # Django's model permission (arena.delete_<model>) still applies; AuditedAdmin's blanket False is bypassed
        # on purpose, so call ModelAdmin's check directly rather than super().
        return (obj is not None and obj.pk is not None and admin.ModelAdmin.has_delete_permission(self,request,obj)
                and self.deletable(obj))
    def get_deleted_objects(self,objs,request):
        summary=self.deletion_summary(objs[0])
        return [f'{k} {v}' for k,v in summary.items()],summary,set(),[]
    def delete_model(self,request,obj):
        self.delete_service(obj.pk,request.user)
    def delete_view(self,request,object_id,extra_context=None):
        try: return super().delete_view(request,object_id,extra_context)
        except ValidationError as e:  # a submission arrived between the confirmation page and the click
            messages.error(request,' '.join(e.messages))
            return redirect(reverse(f'admin:{self.model._meta.app_label}_{self.model._meta.model_name}_change',args=[object_id]))

@admin.register(Contest)
class ContestAdmin(ServiceDeleteMixin,AuditedAdmin):
    list_display=['title','semester','opens_at','closes_at','visible','released_at']
    readonly_fields=['finalized_at','released_at','deletion_status']
    list_filter=['semester','visible']
    def deletable(self,obj): return services.contest_deletable(obj)
    def delete_service(self,pk,actor): services.delete_contest(pk,actor)
    def deletion_summary(self,obj):
        n=services.contest_summary(obj)
        return {'대회':1,'문제':n['problems'],'참가 신청':n['memberships'],'공지사항':n['announcements']}
    @admin.display(description='삭제')
    def deletion_status(self,obj):
        if not obj.pk: return '저장 후 표시됩니다.'
        n=services.contest_summary(obj)['submissions']
        if n: return f'삭제 불가 · 제출 {n}건이 있습니다. 보관하려면 대회 표시를 끄세요.'
        return '삭제 가능 · 제출이 없습니다. 아래 삭제 버튼은 문제·참가 신청·공지·데이터 파일을 함께 지우고 작업 기록에 남깁니다.'

@admin.register(Problem)
class ProblemAdmin(ServiceDeleteMixin,AuditedAdmin):
    list_display=['title','contest','kind','metric','published_at','data_link']
    readonly_fields=['dataset_version','published_at','data_link','deletion_status']
    def data_link(self,obj):
        from django.utils.html import format_html
        return format_html('<a href="/ops/problems/{}/data/">데이터 업로드·검사·미리보기·게시</a>',obj.pk) if obj.pk else '저장 후 데이터 등록'
    data_link.short_description='데이터 관리'
    def get_readonly_fields(self,request,obj=None):
        if obj and (obj.published_at or obj.contest.ended):
            return [f.name for f in self.model._meta.fields]+['data_link','deletion_status']
        return super().get_readonly_fields(request,obj)
    def has_add_permission(self,request): return True
    def deletable(self,obj): return services.problem_deletable(obj)
    def delete_service(self,pk,actor): services.delete_problem(pk,actor)
    def deletion_summary(self,obj): return {'문제':1}
    @admin.display(description='삭제')
    def deletion_status(self,obj):
        if not obj.pk: return '저장 후 표시됩니다.'
        if obj.contest.ended: return '삭제 불가 · 종료된 대회의 문제입니다.'
        n=Submission.objects.filter(problem=obj).count()
        if n: return f'삭제 불가 · 제출 {n}건이 있습니다.'
        return '삭제 가능 · 제출이 없습니다. 아래 삭제 버튼은 데이터 파일을 함께 지우고 작업 기록에 남깁니다.'

class ContestPhaseFilter(admin.SimpleListFilter):
    """Memberships of ended contests cannot change state, so the operator usually wants only open ones."""
    title='대회 진행'
    parameter_name='phase'
    def lookups(self,request,model_admin): return [('open','진행 중·예정'),('ended','종료')]
    def queryset(self,request,queryset):
        if self.value()=='open': return queryset.filter(contest__closes_at__gt=timezone.now())
        if self.value()=='ended': return queryset.filter(contest__closes_at__lte=timezone.now())
        return queryset

class CohortFilter(admin.SimpleListFilter):
    """Student IDs start with the admission year, so the first four characters group a class cohort."""
    title='학번 앞 4자리'
    parameter_name='cohort'
    def lookups(self,request,model_admin):
        prefixes=model_admin.get_queryset(request).annotate(prefix=Substr('student_id',1,4)).order_by('-prefix').values_list('prefix',flat=True).distinct()
        return [(p,p) for p in prefixes if p]
    def queryset(self,request,queryset):
        return queryset.filter(student_id__startswith=self.value()) if self.value() else queryset

@admin.register(Membership)
class MembershipAdmin(AuditedAdmin):
    """Approval desk: filter (phase, contest, status, cohort, date) → sort → header checkbox → bulk action.
    Every column sorts; the status column sorts in workflow order rather than alphabetically."""
    list_display=['contest','student_id','real_name','username','nickname','status_label','created_at']
    list_display_links=['student_id']
    list_filter=[ContestPhaseFilter,('contest',admin.RelatedOnlyFieldListFilter),'status',CohortFilter,('created_at',admin.DateFieldListFilter)]
    list_select_related=['user','contest']
    ordering=['-contest__opens_at',Membership.STATUS_RANK.asc(),'student_id']
    search_fields=['student_id','user__username','user__real_name','user__nickname']
    search_help_text='학번·아이디·이름·별명으로 검색합니다. 필터로 추린 뒤 머리글 확인란으로 전체 선택하고 일괄 작업을 실행하세요.'
    show_facets=admin.ShowFacets.ALWAYS  # filter choices show counts, e.g. 승인 대기 (12)
    readonly_fields=['user','contest','student_id','status']
    list_per_page=200
    list_max_show_all=1000
    actions=['approve_selected','suspend_selected']  # the changelist has select-all; status changes go through the service
    actions_on_bottom=True
    def has_add_permission(self,request): return False
    @admin.display(description='이름',ordering='user__real_name')
    def real_name(self,obj): return obj.user.real_name
    @admin.display(description='아이디',ordering='user__username')
    def username(self,obj): return obj.user.username
    @admin.display(description='별명',ordering='user__nickname')
    def nickname(self,obj): return obj.user.nickname
    @admin.display(description='상태',ordering=Membership.STATUS_RANK)
    def status_label(self,obj): return obj.get_status_display()
    @admin.action(description='선택한 참가자 승인',permissions=['change'])
    def approve_selected(self,request,queryset): self._set_status(request,queryset,'approved')
    @admin.action(description='선택한 참가자 정지',permissions=['change'])
    def suspend_selected(self,request,queryset): self._set_status(request,queryset,'suspended')
    def _set_status(self,request,queryset,state):
        changed=services.set_membership_status(queryset.values_list('pk',flat=True),state,request.user)
        self.message_user(request,f'참가자 {changed}명을 {dict(Membership.STATUS)[state]} 상태로 변경했습니다. 종료된 대회와 이미 같은 상태인 참가자는 제외됩니다.',messages.SUCCESS)

@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display=['id','user','problem','received_at','status','val_score']
    list_filter=['problem','status']
    # Pre-release test results stay out of every screen, including the read-only admin detail view.
    readonly_fields=[f.name for f in Submission._meta.fields if f.name not in ('test_score','test_metrics')]
    exclude=['test_score','test_metrics']
    def has_add_permission(self,request): return False
    def has_change_permission(self,request,obj=None): return False
    def has_delete_permission(self,request,obj=None): return False

@admin.register(Audit)
class AuditAdmin(admin.ModelAdmin):
    list_display=['created_at','actor','action','detail']
    def has_add_permission(self,request): return False
    def has_change_permission(self,request,obj=None): return False
    def has_delete_permission(self,request,obj=None): return False

admin.site.register(Semester,AuditedAdmin)
admin.site.register(Announcement,AuditedAdmin)
