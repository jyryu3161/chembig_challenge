import hashlib
from datetime import timedelta
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from .models import AuthAttempt

class AuthThrottleMiddleware:
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        if request.method == 'POST' and request.path in ['/login/','/signup/','/recover/','/admin/login/']:
            # Nginx overwrites X-Real-IP; the application port is internal in production.
            ip = request.META.get('HTTP_X_REAL_IP', request.META.get('REMOTE_ADDR','unknown'))
            identity = request.POST.get('username', '')[:150].casefold()
            for scope in [f'ip:{ip}', f'account:{identity}']:
                key = hashlib.sha256(scope.encode()).hexdigest()
                with transaction.atomic():
                    # One locked lookup-or-insert: a separate get_or_create() followed by a locked get() let the
                    # reconcile purge delete a stale row in between and turn the login into DoesNotExist.
                    row, _ = AuthAttempt.objects.select_for_update().get_or_create(key=key)
                    if row.window_start < timezone.now()-timedelta(minutes=15):
                        row.count, row.window_start = 0, timezone.now()
                    row.count += 1
                    row.save()
                    if row.count > (1000 if scope.startswith('ip:') else 15):
                        response = HttpResponse('요청이 너무 많습니다. 15분 후 다시 시도하세요.', status=429)
                        response['Retry-After'] = '900'
                        return response
        return self.get_response(request)
