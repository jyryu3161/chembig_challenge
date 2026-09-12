from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django_otp.plugins.otp_totp.models import TOTPDevice
from arena.services import audit
class Command(BaseCommand):
    help='서버 운영자 전용: 관리자 OTP 장치 초기화. 출력 URL은 비밀입니다.'
    def add_arguments(self,parser): parser.add_argument('username')
    def handle(self,username,**kwargs):
        user=get_user_model().objects.filter(username=username,is_staff=True,is_active=True).first()
        if not user: raise CommandError('활성 관리자 계정이 없습니다.')
        TOTPDevice.objects.filter(user=user).delete()
        device=TOTPDevice.objects.create(user=user,name='교수자 인증기',confirmed=True)
        for session in Session.objects.all().iterator():
            if str(session.get_decoded().get('_auth_user_id'))==str(user.pk): session.delete()
        audit(user,'서버 OTP 초기화',user.pk)
        self.stdout.write('인증기 앱에 등록할 비밀 URL (안전하게 보관하고 터미널 기록에서 제거):')
        self.stdout.write(device.config_url)
