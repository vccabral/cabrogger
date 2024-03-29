from django.conf.urls import patterns, include, url
from django.contrib import admin
from django.views.generic import TemplateView

admin.autodiscover()

urlpatterns = patterns('',
    # Examples:
    # url(r'^$', 'cabrogger.views.home', name='home'),
    # url(r'^cabrogger/', include('cabrogger.foo.urls')),
    # url(r'^admin/doc/', include('django.contrib.admindocs.urls')),
    url(r'^$', TemplateView.as_view(template_name='base.html')),
    url(r'^admin/', include(admin.site.urls)),
)
