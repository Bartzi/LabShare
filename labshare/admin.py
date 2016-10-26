from django.contrib import admin
from django.contrib.admin.sites import NotRegistered
from django.contrib.auth import admin as upstream
from django.contrib.auth.forms import UserCreationForm, UserChangeForm
from django.contrib.auth.models import User
from django.utils.translation import ugettext_lazy as _
from guardian.admin import GuardedModelAdmin

from .models import Device, GPU, Reservation, EmailAddress


class DeviceAdmin(GuardedModelAdmin):
    pass

admin.site.register(Device, DeviceAdmin)
admin.site.register(GPU)
admin.site.register(Reservation)
admin.site.register(EmailAddress)


class LabshareUserCreationForm(UserCreationForm):

    def __init__(self, *args, **kwargs):
        super(LabshareUserCreationForm, self).__init__(*args, **kwargs)

        self.fields['email'].required = True


class LabshareUserAdmin(upstream.UserAdmin):
    fieldsets = (
        (None, {'fields': ('username', 'password', 'email')}),
        (_('Personal info'), {'fields': ('first_name', 'last_name')}),
        (_('Permissions'), {'fields': ('is_active', 'is_staff', 'is_superuser',
                                       'groups', 'user_permissions')}),
        (_('Important dates'), {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'password1', 'password2', 'email')}
        ),
    )
    form = UserChangeForm
    add_form = LabshareUserCreationForm

try:
    admin.site.unregister(User)
except NotRegistered:
    pass

admin.site.register(User, LabshareUserAdmin)

