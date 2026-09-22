"""Create (or extend) a contest from a JSON spec plus train/val/test CSVs, e.g. a TDC benchmark task.

Spec example (paths are relative to the spec file):
{
  "semester": {"year": 2026, "term": "2학기"},
  "contest": {"title": "...", "description": "...", "rules": "...", "opens_at": "2026-09-21T18:00:00+09:00",
              "closes_at": "2026-12-04T23:59:59+09:00", "daily_limit": 5, "invite_code": "CODE", "visible": true},
  "problems": [{"title": "...", "kind": "binary", "metric": "roc_auc", "description": "...", "units": "...",
                "source": "...", "split_method": "...", "id_column": "sample_id", "smiles_column": "SMILES",
                "target_column": "target", "feature_columns": [],
                "files": {"train": "train.csv", "val": "val.csv", "test": "test.csv"}, "publish": true}]
}
"""
import json
from datetime import datetime
from pathlib import Path
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from arena.models import Semester, Contest, Problem, User
from arena.services import prepare_data, publish_problem, audit, disk_path

CONTEST_FIELDS = ['title', 'description', 'rules', 'daily_limit', 'invite_code', 'visible']
PROBLEM_FIELDS = ['title', 'kind', 'metric', 'description', 'units', 'source', 'split_method',
                  'id_column', 'smiles_column', 'target_column', 'feature_columns']


def parse_when(value):
    dt = datetime.fromisoformat(value)
    return dt if timezone.is_aware(dt) else timezone.make_aware(dt)


class Command(BaseCommand):
    help = 'JSON 스펙과 CSV 파일로 대회·문제를 만들고 선택적으로 게시합니다.'

    def add_arguments(self, parser):
        parser.add_argument('spec', help='JSON 스펙 파일 경로')
        parser.add_argument('--actor', default='', help='작업 기록에 남길 관리자 username (선택)')
        parser.add_argument('--contest-id', type=int, default=None, help='기존 대회에 문제만 추가할 때 대회 ID')

    def handle(self, *args, **options):
        spec_path = Path(options['spec']).resolve()
        try: spec = json.loads(spec_path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as e: raise CommandError(f'스펙 파일을 읽을 수 없습니다: {e}')
        actor = User.objects.filter(username=options['actor'], is_staff=True).first() if options['actor'] else None
        written = []  # data files this run wrote; removed when the transaction rolls back so no orphans stay on disk
        try:
            with transaction.atomic():
                contest = self.contest(spec, options['contest_id'])
                # Validate every problem and read every file before anything is written to DATA_ROOT.
                plans = [self.plan(contest, item, spec_path.parent) for item in spec.get('problems', [])]
                for problem, raw, item in plans:
                    self.problem(problem, raw, item, actor, written)
        except ValidationError as e:
            self.cleanup(written)
            raise CommandError(' '.join(e.messages))
        except BaseException:
            self.cleanup(written)
            raise
        self.stdout.write(f'완료 · 대회 #{contest.pk} "{contest.title}" · 초대 코드 {contest.invite_code} · '
                          f'마감 {timezone.localtime(contest.closes_at):%Y-%m-%d %H:%M %Z}')

    @staticmethod
    def cleanup(written):
        for rel in written:
            path = disk_path(rel)
            path.unlink(missing_ok=True)
            for folder in path.parents[:2]:  # <pk>/<uuid> then <pk>, only while empty
                try: folder.rmdir()
                except OSError: break

    def contest(self, spec, contest_id):
        if contest_id:
            try: return Contest.objects.get(pk=contest_id)
            except Contest.DoesNotExist: raise CommandError(f'대회 #{contest_id}가 없습니다.')
        sem, con = spec.get('semester'), spec.get('contest')
        if not isinstance(sem, dict) or not isinstance(con, dict): raise CommandError('semester와 contest 항목이 필요합니다.')
        try:
            year, term, title = int(sem['year']), str(sem['term']), con['title']
        except (KeyError, TypeError, ValueError) as e:
            raise CommandError(f'semester.year(정수)·semester.term·contest.title 항목을 확인하세요: {e!r}')
        semester, _ = Semester.objects.get_or_create(year=year, term=term)
        if Contest.objects.filter(title=title, semester=semester).exists():
            raise CommandError(f'같은 학기에 "{title}" 대회가 이미 있습니다. --contest-id로 문제만 추가하세요.')
        try: opens_at, closes_at = parse_when(con['opens_at']), parse_when(con['closes_at'])
        except (KeyError, TypeError, ValueError): raise CommandError('opens_at·closes_at은 ISO 8601 형식이어야 합니다 (예: 2026-12-04T23:59:59+09:00).')
        contest = Contest(semester=semester, opens_at=opens_at, closes_at=closes_at, **{k: con[k] for k in CONTEST_FIELDS if k in con})
        contest.full_clean()
        contest.save()
        return contest

    def plan(self, contest, item, base):
        """(unsaved Problem, {split: bytes}, item) after full_clean and file checks; writes nothing."""
        files = item.get('files') or {}
        if set(files) != {'train', 'val', 'test'}: raise CommandError(f'문제 "{item.get("title")}": files에 train·val·test 경로가 모두 필요합니다.')
        raw = {}
        for split, rel in files.items():
            path = (base / rel).resolve()
            if not path.is_file(): raise CommandError(f'파일이 없습니다: {path}')
            raw[split] = path.read_bytes()
        problem = Problem(contest=contest, **{k: item[k] for k in PROBLEM_FIELDS if k in item})
        problem.full_clean()
        return problem, raw, item

    def problem(self, problem, raw, item, actor, written):
        problem.save()
        problem = prepare_data(problem.pk, raw, actor)
        written.extend(self.paths(problem))
        for warning in problem.warnings: self.stderr.write(f'경고 · {problem.title}: {warning}')
        if item.get('publish'):
            problem = publish_problem(problem.pk, actor)  # revalidates and writes a fresh copy under a new folder
            written.extend(self.paths(problem))
        audit(actor, 'create_contest 명령으로 문제 생성', f'{problem.contest_id}/{problem.pk}')
        self.stdout.write(f'  문제 #{problem.pk} "{problem.title}" · {problem.get_kind_display()} · {problem.metric_label} · '
                          + ' · '.join(f'{s} {problem.manifest[s]["rows"]}행' for s in ('train', 'val', 'test'))
                          + (' · 게시됨' if problem.published_at else ' · 미게시'))

    @staticmethod
    def paths(problem):
        return [entry[k] for entry in problem.manifest.values() for k in ('original', 'student') if k in entry]
