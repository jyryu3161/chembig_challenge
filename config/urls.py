from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path
from arena import views as v
urlpatterns = [
    path('',v.home,name='home'), path('guide/',v.guide,name='guide'),
    path('login/',auth.LoginView.as_view(),name='login'), path('logout/',auth.LogoutView.as_view(),name='logout'),
    path('signup/',v.signup,name='signup'),path('profile/',v.profile,name='profile'),path('recover/',v.recover,name='recover'),
    path('contests/',v.contests,name='contests'),path('contests/<int:pk>/',v.contest,name='contest'),
    path('contests/<int:pk>/join/',v.join,name='join'),path('contests/<int:pk>/<str:tab>/',v.contest,name='contest-tab'),
    path('problems/<int:pk>/submit/',v.submit,name='submit'),path('problems/<int:pk>/files/<str:split>/',v.download,name='download'),
    path('submissions/<uuid:sid>/file/',v.submission_file,name='submission-file'),path('submissions/<uuid:sid>/choose/',v.choose,name='choose'),
    path('ops/',v.operations,name='operations'),path('ops/approve/',v.approve,name='approve'),
    path('ops/recovery/',v.recovery_issue,name='recovery-issue'),path('ops/problems/<int:pk>/data/',v.dataset,name='dataset'),
    path('ops/contests/<int:pk>/clone/',v.clone,name='clone'),path('ops/contests/<int:pk>/export/',v.export,name='export'),
    path('ops/contests/<int:pk>/<str:action>/',v.contest_action,name='contest-action'),
    path('ops/submissions/<uuid:sid>/retry/',v.retry,name='retry'),path('admin/',admin.site.urls),path('health/',v.health,name='health'),
]
