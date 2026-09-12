from django.core.management.base import BaseCommand
from arena.tasks import reconcile
class Command(BaseCommand):
    help='미처리·중단 채점을 PostgreSQL 상태에서 복구합니다.'
    def handle(self,*args,**kwargs):
        reconcile()
        self.stdout.write('채점 작업 재등록 완료')
