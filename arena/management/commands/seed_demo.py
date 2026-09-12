from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.utils import timezone
from arena.models import Semester, Contest, Problem, Announcement
from arena.services import prepare_data,publish_problem
class Command(BaseCommand):
    help='개발 환경에 합성 데이터 데모 대회를 만듭니다. 계정은 생성하지 않습니다.'
    def handle(self,*args,**kwargs):
        if not settings.DEBUG: raise CommandError('DEBUG=1 개발 환경에서만 사용할 수 있습니다.')
        semester,_=Semester.objects.get_or_create(year=2026,term='2학기')
        if Contest.objects.filter(title='ADMET 예측 챌린지 2026').exists(): return
        c=Contest.objects.create(semester=semester,title='ADMET 예측 챌린지 2026',
            description='분자 구조로부터 약물의 물리화학적 특성을 예측해 보세요.\n이 데모 대회는 학습용 합성 데이터로 구성되어 있습니다. 실제 수업에서는 교수자가 제공하는 데이터를 사용합니다.',
            opens_at=timezone.now()-timedelta(days=1),closes_at=timezone.now()+timedelta(days=30),invite_code='CHEMBIG-DEMO',visible=True)
        p=Problem.objects.create(contest=c,title='수용해도 예측 · Solubility',kind='regression',metric='rmse',description='SMILES로부터 수용해도 logS를 예측합니다. 데모용 합성 값입니다.',units='logS (합성)',source='ChemBIG 합성 데모 · 연구에 사용하지 마세요.',split_method='학습용 고정 분할')
        files={s:f'sample_id,SMILES,target\n{s}_001,CCO,-1.2\n{s}_002,CCN,-0.7\n{s}_003,CCC,-2.1\n'.encode() for s in ['train','val','test']}
        prepare_data(p.pk,files,None)
        publish_problem(p.pk,None)
        Announcement.objects.create(contest=c,title='ChemBIG 챌린지에 오신 것을 환영합니다',body='이것은 합성 데이터 데모입니다. 참가 초대 코드는 CHEMBIG-DEMO입니다. 가입 후 관리자 승인이 필요합니다.')
        self.stdout.write('데모 생성 완료 · 초대 코드 CHEMBIG-DEMO')
