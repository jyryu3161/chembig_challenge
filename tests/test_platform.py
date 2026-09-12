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
        with patch('arena.tasks.evaluate',side_effect=OSError('disk')):
            for i in range(4):grade(str(s.pk))
        s.refresh_from_db();self.assertEqual((s.status,s.attempts),('error',4))
        grade(str(s.pk));s.refresh_from_db();self.assertEqual(s.attempts,4)
        services.retry_submission(s.pk,self.u);grade(str(s.pk));grade(str(s.pk));s.refresh_from_db()
        self.assertEqual((s.status,s.attempts,s.val_score),('scored',1,0))
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
