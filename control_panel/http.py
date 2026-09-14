def apply_control_headers(response):
    response['Cache-Control'] = 'private, no-store'
    return response
