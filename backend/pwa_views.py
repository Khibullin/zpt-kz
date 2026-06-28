from pathlib import Path

from django.contrib.staticfiles import finders
from django.http import FileResponse, Http404, HttpResponse


def _static_path(relative_path):
    found = finders.find(relative_path)
    if not found:
        raise Http404(f'{relative_path} not found')
    return Path(found)


def manifest_json(request):
    file_path = _static_path('manifest.json')
    with file_path.open('rb') as manifest_file:
        return HttpResponse(
            manifest_file.read(),
            content_type='application/manifest+json',
        )


def service_worker_js(request):
    file_path = _static_path('service-worker.js')
    response = FileResponse(
        file_path.open('rb'),
        content_type='application/javascript; charset=utf-8',
    )
    response['Service-Worker-Allowed'] = '/'
    response['Cache-Control'] = 'no-cache'
    return response
