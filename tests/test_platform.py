import math
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, close_old_connections
from django.test import TestCase, TransactionTestCase, Client, override_settings
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice
from arena.models import *
from arena import services
from arena.scoring import score, validate_predictions, validate_datasets, read_csv
from arena.tasks import grade, reconcile

class Fixtures:
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = override_settings(DATA_ROOT=Path(self.tmp.name), DEBUG=True, SECURE_SSL_REDIRECT=False,
            PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
        self.cfg.enable()
        self.addCleanup(self.cfg.disable)
        self.addCleanup(self.tmp.cleanup)
        self.dispatch = patch('arena.services.dispatch')
        self.dispatch.start()
        self.addCleanup(self.dispatch.stop)
        self.semester=Semester.objects.create(year=2026,term='2학기')
        self.c=Contest.objects.create(semester=self.semester,title='테스트 대회',description='소개',
            opens_at=timezone.now()-timedelta(days=1),closes_at=timezone.now()+timedelta(days=1),invite_code='CODE',visible=True)
        self.p=Problem.objects.create(contest=self.c,title='용해도',kind='regression',metric='rmse',description='예측',units='logS',source='합성',split_method='고정')
        self.files={s:f'sample_id,SMILES,target,secret\n{s}_001,CCO,0,private\n{s}_002,CCN,1,private\n'.encode() for s in ['train','val','test']}
        services.prepare_data(self.p.pk,self.files,None)
        services.publish_problem(self.p.pk,None)
        self.p.refresh_from_db()
        self.u=User.objects.create_user(username='student',password='strong-password',real_name='비공개실명',student_id='0001',nickname='초록분자')
        self.other=User.objects.create_user(username='other',password='strong-password',real_name='다른실명',student_id='0002',nickname='푸른분자')
        self.m=Membership.objects.create(user=self.u,contest=self.c,student_id=self.u.student_id,status='approved')
        self.raw=b'sample_id,prediction\nval_001,0\nval_002,1\ntest_001,0\ntest_002,1\n'
    def accept(self,raw=None,key=None,at=None,user=None):
        return services.accept_submission(user or self.u,self.p,(self.raw if raw is None else raw),key or uuid.uuid4(),at or timezone.now())
    def finish(self):
        self.c.closes_at=timezone.now()-timedelta(microseconds=1)
        self.c.save(update_fields=['closes_at'])

class ScoringTests(Fixtures, TestCase):
    def test_metrics(self):
        self.assertEqual(score('rmse',[1,2],[1,2]),0)
        self.assertEqual(score('mae',[1,2],[2,3]),1)
        self.assertEqual(score('roc_auc',[0,0,1,1],[.1,.2,.8,.9]),1)
        self.assertEqual(score('ap',[0,0,1,1],[.1,.2,.8,.9]),1)
    def test_reordered_rows_and_string_ids(self):
        raw=b'sample_id,prediction\ntest_002,1\nval_002,1\ntest_001,0\nval_001,0\n'
        s=self.accept(raw);grade(str(s.pk));s.refresh_from_db()
        self.assertEqual(s.val_score,0)
        self.assertIsNone(s.test_score)
        self.assertEqual(validate_predictions(self.p,b'sample_id,prediction\n001,1\n',['001']),{'001':1})
    def test_invalid_csv_matrix(self):
        cases=[b'',b'a,b\nx,1',b'sample_id,prediction\nval_001,0\n',
            self.raw.replace(b'test_002',b'val_002'),self.raw+b'extra,1\n',
            self.raw.replace(b'val_001,0',b'val_001,NaN'),self.raw.replace(b'val_001,0',b'val_001,Infinity'),
            self.raw.replace(b'val_001,0',b'val_001,'),self.raw.replace(b'val_001,0',b'val_001,no'),
            self.raw.replace(b'val_001,0',b'val_001,1,2'),b'\xff',self.raw.replace(b'prediction',b'prediction,prediction')]
        for raw in cases:
            with self.subTest(raw=raw),self.assertRaises(ValidationError): self.accept(raw)
        self.assertEqual(Submission.objects.count(),0)
    def test_probability_bounds(self):
        self.p.kind='binary'
        for value in ['-0.1','1.1']:
            with self.assertRaises(ValidationError):validate_predictions(self.p,self.raw.replace(b'val_001,0',f'val_001,{value}'.encode()),services.expected_ids(self.p))
    def test_dataset_validation(self):
        for changed in [self.files['train'].replace(b'train_002',b'train_001'),self.files['train'].replace(b',0,',b',NaN,'),b'wrong,header\na,b\n']:
            with self.assertRaises(ValidationError):validate_datasets(self.p,{**self.files,'train':changed})
        with self.assertRaises(ValidationError):validate_datasets(self.p,{**self.files,'val':self.files['train']})
        self.p.kind='binary'
        with self.assertRaises(ValidationError):validate_datasets(self.p,{s:raw.replace(b',1,',b',0,') for s,raw in self.files.items()})
        self.assertTrue(self.p.warnings)
    def test_overflow_is_invalid(self):
        with self.assertRaises(ValidationError):self.accept(self.raw.replace(b'val_001,0',b'val_001,1e308'))
    def test_large_finite_predictions_keep_rank_correlation_defined(self):
        # np.std squared 1e200 and overflowed, misreporting a defined Spearman as 'constant predictions'.
        self.assertEqual(score('spearman',[0,1,2],[1e200,0,-1e200]),-1)
        with self.assertRaisesMessage(ValidationError,'수치 범위'):score('pearson',[0,1,2],[1e200,0,-1e200])
        with self.assertRaisesMessage(ValidationError,'모두 같으면'):score('pearson',[0,1,2],[3,3,3])
    def test_internal_metric_error_is_not_blamed_on_the_student(self):
        from arena import scoring
        broken=scoring.Metric('rmse','RMSE','regression',True,lambda y,p:(_ for _ in ()).throw(ValueError('sklearn API changed')))
        with patch.dict(scoring.REGISTRY,{'rmse':broken}):
            with self.assertRaises(ValueError):score('rmse',[0,1],[0,1])  # ranking metric: a server error, never '파일 오류'
            with self.assertRaises(ValueError):scoring.score_all('regression',[0,1],[0,1],primary='rmse')
            with self.assertLogs('arena.scoring',level='ERROR') as log:  # reference metric: logged, stored as None
                details=scoring.score_all('regression',[0,1],[0,1],primary='mae')
        self.assertIsNone(details['rmse']);self.assertEqual(details['mae'],0);self.assertIn('rmse',log.output[0])
    def test_immutable_publication(self):
        with self.assertRaises(ValidationError): services.prepare_data(self.p.pk,self.files,None)
        self.p.metric='mae'
        with self.assertRaises(ValidationError):self.p.full_clean()
    def test_public_file_allowlist(self):
        for split in ['val','test']:
            header,rows=read_csv(services.disk_path(self.p.manifest[split]['student']).read_bytes())
            self.assertEqual(header,['sample_id','SMILES'])
        self.assertIn('target',read_csv(services.disk_path(self.p.manifest['train']['student']).read_bytes())[0])

class WorkflowTests(Fixtures, TestCase):
    def test_quota_and_idempotency(self):
        key=uuid.uuid4();s=self.accept(key=key)
        self.assertEqual(s.pk,self.accept(key=key).pk)
        with self.assertRaises(ValidationError): self.accept(key=key,raw=self.raw.replace(b'val_001,0',b'val_001,1'))
        for _ in range(4):self.accept()
        with self.assertRaises(ValidationError):self.accept()
        Submission.objects.filter(pk=s.pk).update(status='error')
        self.accept()
        self.assertEqual(Submission.objects.count(),6)
    def test_kst_quota_day(self):
        at=timezone.now().replace(hour=16,minute=0,second=0)
        s=self.accept(at=at)
        self.assertEqual(s.quota_day,timezone.localtime(at).date())
    def test_cutoff(self):
        self.accept(at=self.c.closes_at-timedelta(microseconds=1))
        with self.assertRaises(ValidationError):self.accept(at=self.c.closes_at)
        with self.assertRaises(ValidationError):self.accept(at=self.c.opens_at-timedelta(microseconds=1))
        # After the deadline the period message wins even for a malformed file, and nothing is written.
        with self.assertRaisesMessage(ValidationError,'제출 기간이 아닙니다'):self.accept(raw=b'garbage',at=self.c.closes_at)
        self.assertEqual(Submission.objects.count(),1)
    def test_contest_clean_reports_blank_fields_as_validation_error(self):
        with self.assertRaises(ValidationError):Contest(semester=self.semester,title='x',description='d').full_clean()
        now=timezone.now()
        with self.assertRaisesMessage(ValidationError,'종료는 시작 이후'):
            Contest(semester=self.semester,title='x',description='d',invite_code='c',opens_at=now,closes_at=now).full_clean()
        with self.assertRaisesMessage(ValidationError,'1 이상'):
            Contest(semester=self.semester,title='x',description='d',invite_code='c',opens_at=now,closes_at=now+timedelta(days=1),daily_limit=0).full_clean()
    def test_reconcile_purges_stale_auth_attempts(self):
        AuthAttempt.objects.create(key='old',window_start=timezone.now()-timedelta(days=2))
        AuthAttempt.objects.create(key='fresh')
        with patch('arena.tasks.dispatch'):reconcile()
        self.assertEqual(list(AuthAttempt.objects.values_list('key',flat=True)),['fresh'])
    def test_finalization_waits_and_test_secrecy(self):
        s=self.accept();self.finish()
        with self.assertRaises(ValidationError):services.finalize(self.c.pk)
        grade(str(s.pk));services.finalize(self.c.pk)
        s.refresh_from_db();self.assertEqual(s.test_score,0)
        self.assertTrue(FinalChoice.objects.get(submission=s).automatic)
        services.release(self.c.pk,self.u)
        self.c.refresh_from_db();self.assertIsNotNone(self.c.released_at)
        with self.assertRaises(ValidationError):services.choose_final(self.u,s.pk)
    def test_explicit_choice_and_tie_break(self):
        first=self.accept();grade(str(first.pk))
        second=self.accept();grade(str(second.pk))
        self.assertEqual(services.ordered_scores(self.p,Submission.objects.all()).first().pk,first.pk)
        services.choose_final(self.u,second.pk)
        self.finish();services.finalize(self.c.pk)
        self.assertEqual(FinalChoice.objects.get(user=self.u).submission_id,second.pk)
    def test_best_val_auto_choice(self):
        worse=self.accept(self.raw.replace(b'val_001,0',b'val_001,2'));grade(str(worse.pk))
        best=self.accept();grade(str(best.pk))
        self.finish();services.finalize(self.c.pk)
        self.assertEqual(FinalChoice.objects.get(user=self.u).submission_id,best.pk)
    def test_retry_max_and_once_only(self):
        s=self.accept()
        with patch('arena.tasks.evaluate_all',side_effect=OSError('disk')):
            for i in range(4):grade(str(s.pk))
        s.refresh_from_db();self.assertEqual((s.status,s.attempts),('error',4))
        grade(str(s.pk));s.refresh_from_db();self.assertEqual(s.attempts,4)
        services.retry_submission(s.pk,self.u);grade(str(s.pk));grade(str(s.pk));s.refresh_from_db()
        self.assertEqual((s.status,s.attempts,s.val_score),('scored',1,0))
    def test_pending_submission_is_graded_under_current_scorer(self):
        # A SCORER_VERSION bump must not strand pending submissions: grade with the current scorer, stamp it, warn.
        s=self.accept();Submission.objects.filter(pk=s.pk).update(scorer_version='chembig-0')
        with self.assertLogs('arena.tasks',level='WARNING') as log:grade(str(s.pk))
        s.refresh_from_db();self.assertEqual((s.status,s.val_score,s.scorer_version),('scored',0,settings.SCORER_VERSION))
        self.assertIn('chembig-0',log.output[0])
        stale=self.accept();Submission.objects.filter(pk=stale.pk).update(dataset_version='other')
        for _ in range(4):grade(str(stale.pk))
        stale.refresh_from_db();self.assertEqual(stale.status,'error')  # data changed under the receipt: still a server error
    def test_quota_refused_before_parsing(self):
        for _ in range(5):self.accept()
        with patch('arena.services.check_scorable') as scorable:
            with self.assertRaisesMessage(ValidationError,'모두 사용'):self.accept(raw=b'not,a,valid\ncsv')
        scorable.assert_not_called()
    def test_worker_lease_recovery(self):
        s=self.accept()
        Submission.objects.filter(pk=s.pk).update(status='processing',started_at=timezone.now()-timedelta(minutes=6),attempts=1,lease=uuid.uuid4())
        with patch('arena.tasks.dispatch') as dispatch:
            reconcile();dispatch.assert_called_once_with(s.pk)
        grade(str(s.pk));s.refresh_from_db();self.assertEqual(s.status,'scored')
    def test_clone_preserves_archive(self):
        s=self.accept();grade(str(s.pk));self.finish();services.finalize(self.c.pk)
        new_semester=Semester.objects.create(year=2027,term='1학기')
        new=services.clone_contest(self.c.pk,new_semester,self.u)
        self.assertFalse(new.visible);self.assertFalse(new.problems.first().manifest)
        self.assertFalse(Membership.objects.filter(contest=new).exists())
        self.assertEqual(Submission.objects.get(pk=s.pk).test_score,0)
        self.assertEqual(FinalChoice.objects.count(),1)
    def test_recovery_expiry_reuse_sessions(self):
        client=Client();client.force_login(self.u)
        code=services.issue_recovery(self.u,self.other)
        self.assertNotEqual(RecoveryCode.objects.get().digest,code)
        services.redeem_recovery('student',code,'new-very-good-password')
        self.assertEqual(Session.objects.count(),0)
        with self.assertRaises(ValidationError):services.redeem_recovery('student',code,'another-good-password')
        code=services.issue_recovery(self.u,self.other)
        RecoveryCode.objects.filter(used_at__isnull=True).update(expires_at=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(ValidationError):services.redeem_recovery('student',code,'another-good-password')
    def test_recovery_reissue_invalidates_old(self):
        old=services.issue_recovery(self.u,self.other);new=services.issue_recovery(self.u,self.other)
        with self.assertRaises(ValidationError):services.redeem_recovery('student',old,'another-good-password')
        services.redeem_recovery('student',new,'another-good-password')

class AccessTests(Fixtures, TestCase):
    def test_public_and_approved_pages(self):
        for path in ['/','/contests/','/contests/?archive=1','/guide/','/login/','/signup/','/recover/',f'/contests/{self.c.pk}/',f'/contests/{self.c.pk}/rules/']:
            self.assertEqual(self.client.get(path).status_code,200,path)
        for tab in ['problems','leaderboard','submit','submissions']:
            path=f'/contests/{self.c.pk}/{tab}/'
            self.assertEqual(self.client.get(path).status_code,403)
            self.client.force_login(self.u)
            self.assertEqual(self.client.get(path).status_code,200,path)
            self.client.logout()
    def test_no_pii_or_test_score_in_student_pages(self):
        s=self.accept();grade(str(s.pk));Submission.objects.filter(pk=s.pk).update(test_score=987.654321)
        self.client.force_login(self.u)
        for tab in ['leaderboard','submissions']:
            response=self.client.get(f'/contests/{self.c.pk}/{tab}/')
            self.assertNotContains(response,'987.654321');self.assertNotContains(response,'비공개실명')
        self.assertContains(self.client.get(f'/contests/{self.c.pk}/leaderboard/'),'초록분자')
    def test_files_and_other_submission(self):
        s=self.accept();url=f'/problems/{self.p.pk}/files/val/'
        self.assertEqual(self.client.get(url).status_code,403)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code,403)
        self.assertEqual(self.client.get(f'/submissions/{s.pk}/file/').status_code,403)
        self.client.force_login(self.u)
        response=self.client.get(url);self.assertEqual(response.status_code,200)
        self.assertNotIn(b'target',b''.join(response.streaming_content))
        for path in ['/originals/1/train.csv','/media/1.csv',f'/problems/{self.p.pk}/files/original/']:
            self.assertEqual(self.client.get(path).status_code,404)
    def test_x_accel_only_authorized(self):
        self.client.force_login(self.u)
        with override_settings(USE_X_ACCEL=True):
            response=self.client.get(f'/problems/{self.p.pk}/files/val/')
            self.assertTrue(response['X-Accel-Redirect'].startswith('/_protected_student/'))
    def test_unverified_staff_has_no_privileged_access(self):
        self.other.is_staff=True;self.other.save();self.client.force_login(self.other)
        self.assertEqual(self.client.get('/ops/').status_code,302)
        self.assertEqual(self.client.get(f'/problems/{self.p.pk}/files/val/').status_code,403)
        self.assertEqual(self.client.get('/admin/').status_code,302)
    def test_otp_operator_pages(self):
        self.other.is_staff=True;self.other.is_superuser=True;self.other.save()
        device=TOTPDevice.objects.create(user=self.other,name='test',confirmed=True)
        self.client.force_login(self.other)
        session=self.client.session;session['otp_device_id']=device.persistent_id;session.save()
        for path in ['/ops/','/admin/',f'/ops/problems/{self.p.pk}/data/',f'/ops/contests/{self.c.pk}/export/','/ops/recovery/',f'/ops/contests/{self.c.pk}/clone/']:
            self.assertEqual(self.client.get(path).status_code,200,path)
    def test_csrf_and_post_only(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.u)
        self.assertEqual(client.post(f'/contests/{self.c.pk}/join/',{'invite_code':'CODE'}).status_code,403)
        self.assertEqual(self.client.get(f'/contests/{self.c.pk}/join/').status_code,302)
        self.client.force_login(self.u)
        self.assertEqual(self.client.get(f'/contests/{self.c.pk}/join/').status_code,405)
    def test_real_upload(self):
        self.client.force_login(self.u)
        response=self.client.post(f'/problems/{self.p.pk}/submit/',{'request_key':uuid.uuid4(),'file':SimpleUploadedFile('x.csv',self.raw)})
        self.assertEqual(response.status_code,302)
        self.assertEqual(Submission.objects.count(),1)
    def test_join_with_non_ascii_invite_code(self):
        self.c.invite_code='화학-2026';self.c.save(update_fields=['invite_code'])
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(f'/contests/{self.c.pk}/join/',{'invite_code':'틀린 코드'}).status_code,302)
        self.assertFalse(Membership.objects.filter(user=self.other).exists())
        self.assertEqual(self.client.post(f'/contests/{self.c.pk}/join/',{'invite_code':'화학-2026'}).status_code,302)
        self.assertEqual(Membership.objects.get(user=self.other).status,'pending')
    def test_approve_ignores_malformed_member_ids(self):
        self.other.is_staff=True;self.other.is_superuser=True;self.other.save()
        device=TOTPDevice.objects.create(user=self.other,name='test',confirmed=True)
        self.client.force_login(self.other);session=self.client.session;session['otp_device_id']=device.persistent_id;session.save()
        response=self.client.post('/ops/approve/',{'state':'suspended','members':['abc','','\u00b2','\u2460',str(self.m.pk)]})
        self.assertEqual(response.status_code,302);self.m.refresh_from_db();self.assertEqual(self.m.status,'suspended')
        self.assertEqual(self.client.get(f'/contests/{self.c.pk}/leaderboard/?problem=\u00b2').status_code,200)  # as the operator
    def test_duplicate_student_join(self):
        self.other.student_id=self.u.student_id;self.other.save();self.client.force_login(self.other)
        self.client.post(f'/contests/{self.c.pk}/join/',{'invite_code':'CODE'})
        self.assertEqual(Membership.objects.count(),1)
    def test_suspended_cannot_submit_or_choose(self):
        s=self.accept();grade(str(s.pk));self.m.status='suspended';self.m.save()
        with self.assertRaises(PermissionDenied):self.accept()
        with self.assertRaises(PermissionDenied):services.choose_final(self.u,s.pk)
    def test_throttle(self):
        for _ in range(15):self.client.post('/login/',{'username':'unknown','password':'wrong'})
        self.assertEqual(self.client.post('/login/',{'username':'unknown','password':'wrong'}).status_code,429)

class PostgresConcurrencyTests(Fixtures, TransactionTestCase):
    def setUp(self):
        if connection.vendor!='postgresql':self.skipTest('PostgreSQL row locks are required')
        super().setUp()
    def worker(self, key):
        close_old_connections()
        try:
            user=User.objects.get(pk=self.u.pk);p=Problem.objects.get(pk=self.p.pk)
            try:return str(services.accept_submission(user,p,self.raw,key,timezone.now()).pk)
            except ValidationError:return 'limited'
        finally:connection.close()
    def test_twenty_retries_one_receipt(self):
        key=uuid.uuid4()
        with ThreadPoolExecutor(max_workers=20) as pool:results=list(pool.map(self.worker,[key]*20))
        self.assertEqual(len(set(results)),1)
        self.assertEqual(Submission.objects.count(),1)
    def test_twenty_submissions_quota_five(self):
        with ThreadPoolExecutor(max_workers=20) as pool:results=list(pool.map(self.worker,[uuid.uuid4() for _ in range(20)]))
        self.assertEqual(results.count('limited'),15)
        self.assertEqual(Submission.objects.count(),5)
    def test_hundred_authenticated_readers_and_twenty_uploads(self):
        clients=[]
        for n in range(100):
            u=User.objects.create_user(username=f'load{n}',password='load-password',student_id=f'L{n}',real_name='Load',nickname=f'분자{n}')
            Membership.objects.create(user=u,contest=self.c,student_id=u.student_id,status='approved')
            client=Client();client.force_login(u);clients.append(client)
        def work(item):
            n,client=item;close_old_connections()
            try:
                a=client.get(f'/contests/{self.c.pk}/leaderboard/').status_code
                if n<20:
                    b=client.post(f'/problems/{self.p.pk}/submit/',{'request_key':str(uuid.uuid4()),'file':SimpleUploadedFile('p.csv',self.raw)}).status_code
                    return a,b
                return a,200
            finally:connection.close()
        with ThreadPoolExecutor(max_workers=20) as pool:results=list(pool.map(work,enumerate(clients)))
        self.assertTrue(all(a==200 and b in [200,302] for a,b in results))
        self.assertEqual(Submission.objects.count(),20)
        ids=list(Submission.objects.values_list('pk',flat=True))
        with ThreadPoolExecutor(max_workers=20) as pool:list(pool.map(lambda sid: self.grade_and_close(sid),ids+ids))
        self.assertEqual(Submission.objects.filter(status='scored',attempts=1).count(),20)
    def grade_and_close(self,sid):
        close_old_connections()
        try:grade(str(sid))
        finally:connection.close()

class RecoveryQuotaTests(Fixtures, TestCase):
    def test_server_error_repair_after_replacement_quota_used(self):
        failed=self.accept()
        Submission.objects.filter(pk=failed.pk).update(status='error',quota_exempt=True)
        for _ in range(5):
            s=self.accept();grade(str(s.pk))
        services.retry_submission(failed.pk,self.u)
        grade(str(failed.pk))
        failed.refresh_from_db()
        self.assertEqual(failed.status,'scored')
        self.assertTrue(failed.quota_exempt)
        with self.assertRaises(ValidationError):self.accept()
        self.finish();services.finalize(self.c.pk)

class BinaryWorkflowTests(Fixtures, TestCase):
    def test_perfect_binary_auc_end_to_end(self):
        p=Problem.objects.create(contest=self.c,title='분류',kind='binary',metric='roc_auc',description='양성 확률',units='확률',source='합성',split_method='고정')
        services.prepare_data(p.pk,self.files,None);services.publish_problem(p.pk,None);p.refresh_from_db()
        s=services.accept_submission(self.u,p,self.raw,uuid.uuid4(),timezone.now())
        grade(str(s.pk));s.refresh_from_db()
        self.assertEqual(s.val_score,1)
        self.finish();services.finalize(self.c.pk)
        s.refresh_from_db();self.assertEqual(s.test_score,1)

class MetricRegistryTests(Fixtures, TestCase):
    def test_every_metric_kind_is_covered(self):
        from arena.scoring import REGISTRY, metrics_for, score_all
        self.assertEqual({m.kind for m in REGISTRY.values()}, {'regression','binary'})
        self.assertEqual({m.key for m in metrics_for('regression')}, {'rmse','mae','mse','r2','pearson','spearman'})
        self.assertEqual({m.key for m in metrics_for('binary')}, {'roc_auc','ap','log_loss','accuracy','balanced_accuracy','f1','mcc','precision','recall','specificity'})
        self.assertEqual(set(score_all('regression',[1,2,3],[1,2,3])), {m.key for m in metrics_for('regression')})
        self.assertEqual(set(score_all('binary',[0,1],[.2,.8])), {m.key for m in metrics_for('binary')})
    def test_regression_metric_values(self):
        y,p=[1,2,3,4],[1.5,2.5,2.5,4.5]
        self.assertAlmostEqual(score('mse',y,p),0.25);self.assertAlmostEqual(score('rmse',y,p),0.5);self.assertAlmostEqual(score('mae',y,p),0.5)
        self.assertAlmostEqual(score('r2',y,p),1-1/5)
        self.assertAlmostEqual(score('pearson',[1,2,3],[2,4,6]),1);self.assertAlmostEqual(score('spearman',[1,2,3],[1,10,100]),1)
        self.assertAlmostEqual(score('spearman',[1,2,3],[3,2,1]),-1)
        for metric in ['pearson','spearman']:
            with self.assertRaises(ValidationError):score(metric,[1,2,3],[5,5,5])
    def test_binary_metric_values(self):
        y,p=[0,0,1,1],[.1,.6,.4,.9]
        self.assertAlmostEqual(score('accuracy',y,p),0.5);self.assertAlmostEqual(score('precision',y,p),0.5)
        self.assertAlmostEqual(score('recall',y,p),0.5);self.assertAlmostEqual(score('specificity',y,p),0.5)
        self.assertAlmostEqual(score('f1',y,p),0.5);self.assertAlmostEqual(score('balanced_accuracy',y,p),0.5);self.assertAlmostEqual(score('mcc',y,p),0)
        self.assertAlmostEqual(score('roc_auc',y,p),0.75);self.assertGreater(score('log_loss',y,p),0)
        self.assertTrue(math.isfinite(score('log_loss',[0,1],[0,1])))
        self.assertEqual(score('precision',[0,1],[.1,.2]),0)
    def test_problem_metric_must_match_kind(self):
        for kind,metric in [('regression','roc_auc'),('binary','rmse'),('binary','spearman'),('regression','f1')]:
            with self.assertRaises(ValidationError):
                Problem(contest=self.c,title='x',kind=kind,metric=metric,description='d',units='u',source='s',split_method='m').full_clean()
        Problem(contest=self.c,title='x',kind='regression',metric='spearman',description='d',units='u',source='s',split_method='m').full_clean()
        p=Problem(contest=self.c,title='x',kind='binary',metric='mcc',description='d',units='u',source='s',split_method='m')
        self.assertFalse(p.minimize);self.assertEqual(p.all_metrics[0].key,'mcc');self.assertNotIn('mcc',[m.key for m in p.secondary_metrics])
        self.assertTrue(Problem(kind='binary',metric='log_loss').minimize)
    def test_detail_metrics_stored_for_val_and_test(self):
        s=self.accept();grade(str(s.pk));s.refresh_from_db()
        self.assertEqual(s.val_score,0);self.assertEqual(s.val_metrics['rmse'],0);self.assertEqual(s.val_metrics['mae'],0)
        self.assertAlmostEqual(s.val_metrics['r2'],1);self.assertAlmostEqual(s.val_metrics['pearson'],1);self.assertAlmostEqual(s.val_metrics['spearman'],1)
        self.assertEqual(s.test_metrics,{})
        self.finish();services.finalize(self.c.pk);s.refresh_from_db()
        self.assertEqual(s.test_score,0);self.assertEqual(s.test_metrics['rmse'],0);self.assertEqual(s.test_metrics['r2'],1)
    def test_undefined_secondary_metric_is_none_not_failure(self):
        s=self.accept(self.raw.replace(b'val_002,1',b'val_002,0').replace(b'test_002,1',b'test_002,0'));grade(str(s.pk));s.refresh_from_db()
        self.assertEqual(s.status,'scored');self.assertIsNone(s.val_metrics['pearson']);self.assertIsNone(s.val_metrics['spearman'])
    def test_undefined_primary_metric_rejected_without_quota(self):
        p=Problem.objects.create(contest=self.c,title='상관',kind='regression',metric='spearman',description='d',units='u',source='s',split_method='m')
        services.prepare_data(p.pk,self.files,None);services.publish_problem(p.pk,None);p.refresh_from_db()
        constant=self.raw.replace(b'val_002,1',b'val_002,0').replace(b'test_002,1',b'test_002,0')
        with self.assertRaises(ValidationError):services.accept_submission(self.u,p,constant,uuid.uuid4(),timezone.now())
        self.assertEqual(Submission.objects.filter(problem=p).count(),0)
        s=services.accept_submission(self.u,p,self.raw,uuid.uuid4(),timezone.now());grade(str(s.pk));s.refresh_from_db()
        self.assertAlmostEqual(s.val_score,1)
    def test_binary_details_and_leaderboard_columns(self):
        p=Problem.objects.create(contest=self.c,title='분류',kind='binary',metric='ap',description='양성 확률',units='확률',source='합성',split_method='고정')
        services.prepare_data(p.pk,self.files,None);services.publish_problem(p.pk,None);p.refresh_from_db()
        s=services.accept_submission(self.u,p,self.raw.replace(b',1',b',0.9').replace(b',0\n',b',0.2\n'),uuid.uuid4(),timezone.now())
        grade(str(s.pk));s.refresh_from_db()
        self.assertEqual(s.val_score,1);self.assertEqual(s.val_metrics['accuracy'],1);self.assertEqual(s.val_metrics['mcc'],1);self.assertEqual(s.val_metrics['specificity'],1)
        self.client.force_login(self.u)
        response=self.client.get(f'/contests/{self.c.pk}/leaderboard/?problem={p.pk}')
        for label in ['AUPRC (Average Precision)','ROC-AUC','Balanced Accuracy','Specificity','Log Loss']:self.assertContains(response,label)
        response=self.client.get(f'/contests/{self.c.pk}/submissions/')
        self.assertContains(response,'세부 지표');self.assertContains(response,'MCC')
        self.assertNotContains(response,'test_metrics')
    def test_export_has_detail_metrics(self):
        s=self.accept();grade(str(s.pk))
        self.other.is_staff=True;self.other.is_superuser=True;self.other.save()
        device=TOTPDevice.objects.create(user=self.other,name='test',confirmed=True)
        self.client.force_login(self.other);session=self.client.session;session['otp_device_id']=device.persistent_id;session.save()
        body=self.client.get(f'/ops/contests/{self.c.pk}/export/').content.decode('utf-8-sig')
        self.assertIn('세부 지표',body);self.assertIn('MAE=0',body);self.assertIn('RMSE',body)

    def test_legacy_submission_details_show_stored_ranking_score(self):
        s=self.accept();grade(str(s.pk));Submission.objects.filter(pk=s.pk).update(val_metrics={},val_score=0.42)
        self.client.force_login(self.u);response=self.client.get(f'/contests/{self.c.pk}/submissions/')
        self.assertContains(response,'RMSE: 0.420000 (순위)');self.assertContains(response,'MAE: —')

class DeletionTests(Fixtures, TestCase):
    def operator(self):
        self.other.is_staff=True;self.other.is_superuser=True;self.other.save()
        device=TOTPDevice.objects.create(user=self.other,name='test',confirmed=True)
        self.client.force_login(self.other);session=self.client.session;session['otp_device_id']=device.persistent_id;session.save()
    def folders(self,problem):
        return [Path(self.tmp.name)/prefix/str(problem.pk) for prefix in ('originals','student')]
    def test_contest_without_submissions_is_removed_with_files_and_audited(self):
        Announcement.objects.create(contest=self.c,title='t',body='b')
        self.assertTrue(all(f.is_dir() for f in self.folders(self.p)))
        with self.captureOnCommitCallbacks(execute=True):services.delete_contest(self.c.pk,self.other)
        for model in (Contest,Problem,Membership,Announcement):self.assertFalse(model.objects.exists(),model)
        self.assertFalse(any(f.exists() for f in self.folders(self.p)));self.assertTrue(Semester.objects.filter(pk=self.semester.pk).exists())
        self.assertTrue(Audit.objects.filter(action='대회 삭제',actor=self.other,detail__contains='문제 1개 · 참가 신청 1건 · 공지 1건').exists())
    def test_contest_with_submissions_is_protected(self):
        s=self.accept()
        with self.assertRaisesMessage(ValidationError,'제출이 있는 대회'):services.delete_contest(self.c.pk,self.other)
        self.assertTrue(Contest.objects.filter(pk=self.c.pk).exists());self.assertTrue(all(f.is_dir() for f in self.folders(self.p)))
        with self.assertRaisesMessage(ValidationError,'제출이 있는 문제'):services.delete_problem(self.p.pk,self.other)
        self.assertTrue(Submission.objects.filter(pk=s.pk).exists())
    def test_problem_deletion_rules(self):
        p2=Problem.objects.create(contest=self.c,title='실수',kind='binary',metric='roc_auc',description='d',units='u',source='s',split_method='m')
        services.prepare_data(p2.pk,self.files,None)
        with self.captureOnCommitCallbacks(execute=True):services.delete_problem(p2.pk,self.other)
        self.assertFalse(Problem.objects.filter(pk=p2.pk).exists());self.assertFalse(any(f.exists() for f in self.folders(p2)))
        self.assertTrue(all(f.is_dir() for f in self.folders(self.p)))  # sibling problem untouched
        self.finish()
        with self.assertRaisesMessage(ValidationError,'종료된 대회'):services.delete_problem(self.p.pk,self.other)
    def test_admin_delete_button_follows_the_rule(self):
        self.operator();change=f'/admin/arena/contest/{self.c.pk}/change/';delete=f'/admin/arena/contest/{self.c.pk}/delete/'
        response=self.client.get(change);self.assertContains(response,'삭제 가능');self.assertContains(response,delete)
        self.assertNotContains(self.client.get('/admin/arena/contest/'),'delete_selected')
        response=self.client.get(delete);self.assertEqual(response.status_code,200);self.assertContains(response,'참가 신청 1')
        self.accept()  # a student submits while the confirmation page is open
        self.assertEqual(self.client.post(delete,{'post':'yes'}).status_code,403)
        from arena.admin import ContestAdmin
        with patch.object(ContestAdmin,'deletable',return_value=True):  # permission passed just before the submission landed
            response=self.client.post(delete,{'post':'yes'});self.assertRedirects(response,change,fetch_redirect_response=False)
        self.assertTrue(Contest.objects.filter(pk=self.c.pk).exists())
        response=self.client.get(change);self.assertContains(response,'제출이 있는 대회');self.assertContains(response,'삭제 불가 · 제출 1건')
        self.assertNotContains(response,f'href="{delete}"');self.assertEqual(self.client.get(delete).status_code,403)
        self.assertNotContains(self.client.get('/ops/'),delete)
    def test_admin_delete_requires_model_permission(self):
        from django.contrib.auth.models import Permission
        ta=User.objects.create_user(username='ta',password='strong-password',real_name='조교',student_id='9',nickname='ta',is_staff=True)
        ta.user_permissions.add(*Permission.objects.filter(content_type__app_label='arena').exclude(codename__startswith='delete_'))
        device=TOTPDevice.objects.create(user=ta,name='test',confirmed=True)
        self.client.force_login(ta);session=self.client.session;session['otp_device_id']=device.persistent_id;session.save()
        change=f'/admin/arena/contest/{self.c.pk}/change/';delete=f'/admin/arena/contest/{self.c.pk}/delete/'
        response=self.client.get(change);self.assertEqual(response.status_code,200);self.assertNotContains(response,f'href="{delete}"')
        self.assertEqual(self.client.get(delete).status_code,403);self.assertEqual(self.client.post(delete,{'post':'yes'}).status_code,403)
        self.assertTrue(Contest.objects.filter(pk=self.c.pk).exists())
    def test_submission_during_deletion_is_refused_not_500(self):
        def delete_contest_meanwhile(problem,raw):services.delete_contest(self.c.pk,self.other)
        with patch('arena.services.check_scorable',side_effect=delete_contest_meanwhile):
            with self.assertRaisesMessage(ValidationError,'삭제'):self.accept()
        self.assertEqual(Submission.objects.count(),0);self.assertFalse(list(Path(self.tmp.name).rglob('submissions/*/*.csv')))
        c2=Contest.objects.create(semester=self.semester,title='둘',description='d',opens_at=timezone.now()-timedelta(days=1),closes_at=timezone.now()+timedelta(days=1),invite_code='B',visible=True)
        p2=Problem.objects.create(contest=c2,title='p',kind='regression',metric='rmse',description='d',units='u',source='s',split_method='m')
        services.prepare_data(p2.pk,self.files,None);services.publish_problem(p2.pk,None);p2.refresh_from_db()
        Membership.objects.create(user=self.u,contest=c2,student_id=self.u.student_id,status='approved')
        def delete_problem_meanwhile(problem,raw):services.delete_problem(p2.pk,self.other)
        with patch('arena.services.check_scorable',side_effect=delete_problem_meanwhile):
            with self.assertRaisesMessage(ValidationError,'삭제'):services.accept_submission(self.u,p2,self.raw,uuid.uuid4(),timezone.now())
        self.assertEqual(Submission.objects.count(),0)
    def test_admin_deletes_an_unused_contest(self):
        self.operator()
        c2=Contest.objects.create(semester=self.semester,title='시험용',description='d',opens_at=timezone.now(),closes_at=timezone.now()+timedelta(days=1),invite_code='TMP')
        p2=Problem.objects.create(contest=c2,title='p',kind='regression',metric='mae',description='d',units='u',source='s',split_method='m')
        services.prepare_data(p2.pk,self.files,None)
        self.assertContains(self.client.get('/ops/'),f'/admin/arena/contest/{c2.pk}/delete/')
        with self.captureOnCommitCallbacks(execute=True):
            response=self.client.post(f'/admin/arena/contest/{c2.pk}/delete/',{'post':'yes'})
        self.assertEqual(response.status_code,302);self.assertFalse(Contest.objects.filter(pk=c2.pk).exists())
        self.assertFalse(any(f.exists() for f in self.folders(p2)));self.assertTrue(all(f.is_dir() for f in self.folders(self.p)))
        self.assertTrue(Audit.objects.filter(action='대회 삭제',detail__contains='시험용').exists())
        response=self.client.get(f'/admin/arena/problem/{self.p.pk}/change/');self.assertContains(response,'삭제 가능')
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.post(f'/admin/arena/problem/{self.p.pk}/delete/',{'post':'yes'}).status_code,302)
        self.assertFalse(Problem.objects.filter(pk=self.p.pk).exists());self.assertTrue(Audit.objects.filter(action='문제 삭제').exists())

class BackfillMetricsCommandTests(Fixtures, TestCase):
    def test_fills_details_without_touching_scores(self):
        from io import StringIO
        from django.core.management import call_command
        s=self.accept();grade(str(s.pk))
        Submission.objects.filter(pk=s.pk).update(val_metrics={},scorer_version='chembig-1')
        stale=self.accept(self.raw.replace(b'val_001,0',b'val_001,2'));grade(str(stale.pk))
        Submission.objects.filter(pk=stale.pk).update(val_metrics={},val_score=123.0)  # stored score no longer reproducible
        out,err=StringIO(),StringIO()
        call_command('backfill_metrics','--dry-run',stdout=out,stderr=err)
        s.refresh_from_db();self.assertEqual(s.val_metrics,{});self.assertIn('보충 1건',out.getvalue());self.assertIn('건너뜀 1건',out.getvalue())
        call_command('backfill_metrics',stdout=out,stderr=err)
        s.refresh_from_db();stale.refresh_from_db()
        self.assertEqual(s.val_score,0);self.assertEqual(s.scorer_version,'chembig-1');self.assertEqual(s.val_metrics['rmse'],0);self.assertAlmostEqual(s.val_metrics['r2'],1)
        self.assertEqual(s.test_metrics,{})
        self.assertEqual((stale.val_score,stale.val_metrics),(123.0,{}));self.assertIn(str(stale.pk),err.getvalue())
        self.assertTrue(Audit.objects.filter(action__contains='backfill_metrics').exists())
        self.finish();services.finalize(self.c.pk);s.refresh_from_db();self.assertEqual(s.test_metrics['rmse'],0)

class CreateContestCommandTests(Fixtures, TestCase):
    def test_spec_creates_publishes_and_scores(self):
        import json
        from django.core.management import call_command
        folder=Path(self.tmp.name)/'spec';folder.mkdir()
        for split,raw in self.files.items():(folder/f'{split}.csv').write_bytes(raw)
        spec={'semester':{'year':2030,'term':'1학기'},'contest':{'title':'TDC 테스트','description':'d','rules':'r',
            'opens_at':'2030-01-01T00:00:00+09:00','closes_at':'2030-12-04T23:59:59+09:00','daily_limit':3,'invite_code':'TDC-1','visible':True},
            'problems':[{'title':'BBBP','kind':'binary','metric':'roc_auc','description':'d','units':'u','source':'s','split_method':'m',
                'files':{'train':'train.csv','val':'val.csv','test':'test.csv'},'publish':True}]}
        (folder/'spec.json').write_text(json.dumps(spec,ensure_ascii=False),encoding='utf-8')
        call_command('create_contest',str(folder/'spec.json'))
        c=Contest.objects.get(title='TDC 테스트');p=c.problems.get()
        self.assertEqual(timezone.localtime(c.closes_at).strftime('%Y-%m-%d %H:%M:%S'),'2030-12-04 23:59:59')
        self.assertEqual(c.daily_limit,3);self.assertTrue(c.visible);self.assertIsNotNone(p.published_at)
        self.assertEqual(p.manifest['train']['rows'],2);self.assertEqual(p.metric,'roc_auc');self.assertTrue(Audit.objects.filter(action__contains='create_contest').exists())
        Membership.objects.create(user=self.u,contest=c,student_id=self.u.student_id,status='approved')
        s=services.accept_submission(self.u,p,self.raw,uuid.uuid4(),c.opens_at);grade(str(s.pk));s.refresh_from_db();self.assertEqual(s.val_score,1)
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):call_command('create_contest',str(folder/'spec.json'))
        spec['problems'][0]['metric']='rmse';(folder/'spec.json').write_text(json.dumps(spec),encoding='utf-8')
        with self.assertRaises(CommandError):call_command('create_contest',str(folder/'spec.json'),'--contest-id',str(c.pk))
        self.assertEqual(c.problems.count(),1)
    def test_failed_spec_leaves_no_files_or_rows(self):
        import json
        from django.core.management import call_command
        from django.core.management.base import CommandError
        folder=Path(self.tmp.name)/'spec';folder.mkdir()
        for split,raw in self.files.items():(folder/f'{split}.csv').write_bytes(raw)
        good={'title':'A','kind':'binary','metric':'roc_auc','description':'d','units':'u','source':'s','split_method':'m',
            'files':{'train':'train.csv','val':'val.csv','test':'test.csv'},'publish':True}
        spec={'semester':{'year':2031,'term':'1학기'},'contest':{'title':'실패','description':'d','rules':'r','opens_at':'2031-01-01T00:00:00+09:00',
            'closes_at':'2031-12-04T23:59:59+09:00','invite_code':'X'},'problems':[good,{**good,'title':'B','metric':'rmse'}]}
        files_before=sorted(str(f) for f in Path(self.tmp.name).rglob('*.csv'))
        def run():
            (folder/'spec.json').write_text(json.dumps(spec,ensure_ascii=False),encoding='utf-8')
            call_command('create_contest',str(folder/'spec.json'))
        with self.assertRaises(CommandError):run()  # second problem fails full_clean before anything is written
        spec['problems'].pop()
        with patch('arena.management.commands.create_contest.publish_problem',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):run()  # failure after prepare_data wrote files: they must be removed again
        self.assertFalse(Contest.objects.filter(title='실패').exists());self.assertEqual(Problem.objects.exclude(pk=self.p.pk).count(),0)
        self.assertEqual(sorted(str(f) for f in Path(self.tmp.name).rglob('*.csv')),files_before)
        spec['contest']['opens_at']=None
        with self.assertRaisesMessage(CommandError,'ISO 8601'):run()
        spec['contest']['opens_at']=1760000000
        with self.assertRaisesMessage(CommandError,'ISO 8601'):run()
        spec['contest']['opens_at']='2031-01-01T00:00:00+09:00';spec['contest']['name']=spec['contest'].pop('title')
        with self.assertRaisesMessage(CommandError,'contest.title'):run()
