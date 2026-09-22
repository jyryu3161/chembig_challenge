"""Fill val_metrics/test_metrics for submissions scored before the metric registry existed (scorer chembig-1).

Only the detail metrics are written. val_score/test_score are never changed: when the recomputed ranking metric
does not reproduce the stored score, the submission is reported and left untouched.
"""
import math
from django.core.management.base import BaseCommand
from arena.models import Submission, User
from arena.services import audit, disk_path, evaluate_all

SPLITS = (('val', 'val_score', 'val_metrics'), ('test', 'test_score', 'test_metrics'))


class Command(BaseCommand):
    help = '세부 지표 없이 채점된 제출의 val_metrics·test_metrics를 보충합니다. 점수는 변경하지 않습니다.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='변경 없이 대상만 출력')
        parser.add_argument('--actor', default='', help='작업 기록에 남길 관리자 username (선택)')

    def handle(self, *args, **options):
        actor = User.objects.filter(username=options['actor'], is_staff=True).first() if options['actor'] else None
        filled = skipped = 0
        for s in Submission.objects.filter(status='scored').select_related('problem').iterator():
            updates = {}
            for split, score_field, metrics_field in SPLITS:
                stored = getattr(s, score_field)
                if stored is None or getattr(s, metrics_field): continue
                try:
                    primary, details = evaluate_all(s.problem, disk_path(s.path).read_bytes(), split)
                except Exception as e:  # unreadable file, changed data, undefined metric: report, never guess
                    self.stderr.write(f'{s.pk} {split}: 계산 실패 · {e}')
                    skipped += 1
                    continue
                if not math.isclose(primary, stored, rel_tol=1e-9, abs_tol=1e-12):
                    self.stderr.write(f'{s.pk} {split}: 저장 점수 {stored!r} ≠ 재계산 {primary!r} (scorer {s.scorer_version}) · 건너뜀')
                    skipped += 1
                    continue
                details[s.problem.metric] = stored  # keep the ranking value exactly as stored
                updates[metrics_field] = details
            if updates:
                filled += 1
                if not options['dry_run']: Submission.objects.filter(pk=s.pk).update(**updates)
        if filled and not options['dry_run']: audit(actor, 'backfill_metrics 세부 지표 보충', f'{filled}건')
        self.stdout.write(f'{"(dry-run) " if options["dry_run"] else ""}보충 {filled}건 · 건너뜀 {skipped}건')
