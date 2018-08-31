from asgiref.sync import async_to_sync
from channels.generic.websocket import WebsocketConsumer

from labshare.models import Device
from labshare.utils import publish_device_state


class GPUInfoUpdater(WebsocketConsumer):
    def connect(self):
        self.user = self.scope['user']
        self.device_name = self.scope['url_route']['kwargs']['device_name']

        device = Device.objects.get(name=self.device_name)
        if device.can_be_used_by(self.user):
            async_to_sync(self.channel_layer.group_add)(
                self.device_name,
                self.channel_name,
            )

            publish_device_state(device, self.channel_name)

            self.accept()

    def disconnect(self, message, **kwargs):
        async_to_sync(self.channel_layer.group_discard)(
            self.device_name,
            self.channel_name,
        )

    def update_info(self, event):
        self.send(text_data=event['message'])
