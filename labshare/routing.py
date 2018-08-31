from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.urls import path

from labshare.consumers import GPUInfoUpdater


websocket_urlpatterns = [
    path('ws/device/<device_name>/', GPUInfoUpdater),
]


application = ProtocolTypeRouter({
    'websocket': AuthMiddlewareStack(
        URLRouter(
            websocket_urlpatterns
        )
    )
})
