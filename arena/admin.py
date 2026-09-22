from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.urls import reverse
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

@admin.register(Membership)
class MembershipAdmin(AuditedAdmin):
    list_display=['user','contest','student_id','status']
    list_filter=['contest','status']
    readonly_fields=['user','contest','student_id','status']
    def has_add_permission(self,request): return False

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
