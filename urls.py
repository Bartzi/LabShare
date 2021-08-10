from django.conf.urls import include, url
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from labshare import views

urlpatterns = [
    path('admin/', include(admin.site.urls[:2], namespace=admin.site.urls[-1])),
    path('', views.index, name="index"),
    path('message', views.send_message, name="send_message"),
    path('gpu/update', views.update_gpu_info, name="update_gpu_info"),

    path('accounts/login', auth_views.LoginView.as_view(template_name='login.html')),
    path('login/', auth_views.LoginView.as_view(template_name='login.html')),
    path('view-as', views.view_as, name="view_as"),
    path('hijack/', include('hijack.urls', namespace='hijack')),
    path('', include('django.contrib.auth.urls')),
]
