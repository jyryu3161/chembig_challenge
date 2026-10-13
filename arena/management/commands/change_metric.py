"""Change the ranking metric of a published problem: manage.py change_metric <problem_id> <metric> [--actor username]

Scores of every graded submission are re-derived from the stored detail metrics (see services.change_ranking_metric);
run backfill_metrics first if some submissions were graded before the metric existed.
"""
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from arena.models import Problem, User
from arena.scoring import REGISTRY
from arena.services import change_ranking_metric


class Command(BaseCommand):
    help = '게시된 문제의 순위 지표를 바꾸고 채점 완료 제출의 순위 점수를 세부 지표에서 다시 산정합니다.'

    def add_arguments(self, parser):
        parser.add_argument('problem_id', type=int)
        parser.add_argument('metric', help='순위 지표 키 (예: mcc, roc_auc, rmse)')
        parser.add_argument('--actor', default='', help='작업 기록에 남길 관리자 username (선택)')

    def handle(self, *args, **options):
        actor = User.objects.filter(username=options['actor'], is_staff=True).first() if options['actor'] else None
        try:
            n = change_ranking_metric(options['problem_id'], options['metric'], actor)
        except Problem.DoesNotExist:
            raise CommandError(f'문제 {options["problem_id"]}이(가) 없습니다.')
        except ValidationError as e:
            raise CommandError(' '.join(e.messages))
        self.stdout.write(f'문제 {options["problem_id"]}의 순위 지표를 {REGISTRY[options["metric"]].label}(으)로 바꾸고 제출 {n}건의 순위 점수를 다시 산정했습니다.')
