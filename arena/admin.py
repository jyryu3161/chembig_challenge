from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django_otp.admin import OTPAdminSite
from .models import User, Semester, Contest, Membership, Problem, Submission, Audit, Announcement

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

@admin.register(Contest)
class ContestAdmin(AuditedAdmin):
    list_display=['title','semester','opens_at','closes_at','visible','released_at']
    readonly_fields=['finalized_at','released_at']
    list_filter=['semester','visible']

@admin.register(Problem)
class ProblemAdmin(AuditedAdmin):
    list_display=['title','contest','kind','metric','published_at','data_link']
    readonly_fields=['dataset_version','published_at','data_link']
    def data_link(self,obj):
        from django.utils.html import format_html
        return format_html('<a href="/ops/problems/{}/data/">데이터 업로드·검사·미리보기·게시</a>',obj.pk) if obj.pk else '저장 후 데이터 등록'
    data_link.short_description='데이터 관리'
    def get_readonly_fields(self,request,obj=None):
        if obj and (obj.published_at or obj.contest.ended):
            return [f.name for f in self.model._meta.fields]+['data_link']
        return super().get_readonly_fields(request,obj)
    def has_add_permission(self,request): return True

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
    readonly_fields=[f.name for f in Submission._meta.fields if f.name!='test_score']
    exclude=['test_score']
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
