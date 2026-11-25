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
    private_media = False
    if enabled():
        resolved_parts = file_path.relative_to(media_root).parts
        public_image = (resolved_parts and resolved_parts[0] in {
            'products', 'seller_logos', 'maintenance_kits', 'seller_directory'
        } and file_path.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif'})
        if not public_image:
            from core.office_views import office_access
            if is_storefront(request) or not request.user.is_authenticated or not office_access(request.user):
                raise Http404('File not found')
        private_media = not is_storefront(request)
    content_type, _ = mimetypes.guess_type(str(file_path))
    response = FileResponse(
        file_path.open('rb'),
        as_attachment=private_media and not (content_type or '').startswith('image/'),
        filename=file_path.name,
        content_type=content_type or 'application/octet-stream',
    )
    response['X-Content-Type-Options'] = 'nosniff'
    if private_media:
        from core.office_privacy import protect_office_response
        if (content_type or '').startswith('image/') and content_type != 'image/svg+xml':
            response['Cache-Control'] = 'private, max-age=3600'
            response.office_image_cache = True
        if content_type == 'image/svg+xml':
            response['Content-Disposition'] = 'attachment'
        protect_office_response(response)
    else:
        response['Cache-Control'] = 'public, max-age=86400, stale-while-revalidate=604800'
    return response
