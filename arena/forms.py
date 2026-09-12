from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import User, Semester

class SignupForm(UserCreationForm):
    class Meta:
        model = User
        fields = ['username','real_name','student_id','nickname','password1','password2']
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in ['real_name','student_id','nickname']: self.fields[field].required = True

class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['nickname','show_real_name']

class JoinForm(forms.Form):
    invite_code = forms.CharField(label='수업 초대 코드', max_length=80)

class UploadForm(forms.Form):
    request_key = forms.UUIDField(widget=forms.HiddenInput)
    file = forms.FileField(label='예측 CSV 파일', widget=forms.FileInput(attrs={'accept':'.csv,text/csv'}))

class DatasetForm(forms.Form):
    train = forms.FileField(label='train CSV (정답 포함)')
    val = forms.FileField(label='val CSV (정답 포함)')
    test = forms.FileField(label='test CSV (정답 포함)')

class RecoveryForm(forms.Form):
    username = forms.CharField(label='로그인 ID', max_length=150)
    code = forms.CharField(label='관리자가 전달한 일회용 코드', max_length=100)
    password = forms.CharField(label='새 비밀번호', widget=forms.PasswordInput)
    password_confirm = forms.CharField(label='새 비밀번호 확인', widget=forms.PasswordInput)
    def clean(self):
        data = super().clean()
        if data.get('password') != data.get('password_confirm'): raise forms.ValidationError('비밀번호가 일치하지 않습니다.')
        return data

class CloneForm(forms.Form):
    semester = forms.ModelChoiceField(label='새 학년도·학기', queryset=Semester.objects.all())
