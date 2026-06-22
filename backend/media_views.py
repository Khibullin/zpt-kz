import mimetypes
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404


def serve_media(request, path):
    safe_path = Path(path)
    if '..' in safe_path.parts:
        raise Http404('Invalid path')

    media_root = settings.MEDIA_ROOT.resolve()
    file_path = (settings.MEDIA_ROOT / safe_path).resolve()

    if not str(file_path).startswith(str(media_root)) or not file_path.is_file():
        raise Http404('File not found')

    content_type, _ = mimetypes.guess_type(str(file_path))
    response = FileResponse(
        file_path.open('rb'),
        content_type=content_type or 'application/octet-stream',
    )
    response['Cache-Control'] = (
        'public, max-age=86400, stale-while-revalidate=604800'
    )
    return response
