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

    if not file_path.is_relative_to(media_root) or not file_path.is_file():
        raise Http404('File not found')

    from core.domain_boundary import enabled, is_storefront
    if enabled():
        public_image = (safe_path.parts and safe_path.parts[0] in {
            'products', 'seller_logos', 'maintenance_kits', 'seller_directory'
        } and file_path.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif'})
        if not public_image:
            from core.office_views import office_access
            if is_storefront(request) or not request.user.is_authenticated or not office_access(request.user):
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
