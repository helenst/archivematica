from django.conf import settings
from django.conf.urls import include, url

from . import views


urlpatterns = [
    url(
        r'^$',
        views.dashboard,
        name='dashboard'
    ),

    url(
        r'^transfers/$',
        views.transfers,
        name='transfers'
    ),

    url(
        r'^transfers/(?P<uuid>' + settings.UUID_REGEX + ')/progress$',
        views.transfer_progress,
        name='transfer_progress'
    ),
]
