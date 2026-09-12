import logging
import uuid
from datetime import timedelta
from celery import shared_task
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from .models import Submission
from .services import disk_path, evaluate, dispatch
logger = logging.getLogger(__name__)

@shared_task
def grade(sid):
    token = uuid.uuid4()
    with transaction.atomic():
        s = Submission.objects.select_for_update().get(pk=sid)
        if s.status != 'pending': return
        s.status, s.lease, s.started_at = 'processing', token, timezone.now()
        s.attempts += 1
        s.save(update_fields=['status','lease','started_at','attempts'])
    try:
        s = Submission.objects.select_related('problem').get(pk=sid)
        if s.dataset_version != s.problem.dataset_version or s.scorer_version != settings.SCORER_VERSION:
            raise RuntimeError('Version mismatch')
        value = evaluate(s.problem, disk_path(s.path).read_bytes(), 'val')
        Submission.objects.filter(pk=sid, status='processing', lease=token).update(status='scored', val_score=value, error='', lease=None)
    except Exception:
        logger.exception('Scoring failed for submission %s', sid)
        status = 'pending' if s.attempts < 4 else 'error'
        Submission.objects.filter(pk=sid, status='processing', lease=token).update(
            status=status, lease=None, quota_exempt=s.quota_exempt or status == 'error', error='일시적인 서버 오류입니다. 자동 재시도 후에도 실패하면 관리자에게 문의하세요.')
        # Reconciliation supplies retries without depending on Redis persistence.


def reconcile():
    stale = timezone.now()-timedelta(minutes=5)
    with transaction.atomic():
        for s in Submission.objects.select_for_update().filter(status='processing', started_at__lt=stale):
            s.status = 'pending' if s.attempts < 4 else 'error'
            s.lease = None
            s.quota_exempt = s.quota_exempt or s.status == 'error'
            s.error = '작업 중단으로 재처리가 필요합니다.'
            s.save(update_fields=['status','lease','error','quota_exempt'])
    for sid in Submission.objects.filter(status='pending').values_list('pk', flat=True): dispatch(sid)
