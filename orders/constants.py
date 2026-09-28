from core.kazakhstan_locations import KAZAKHSTAN_CITIES

SESSION_CART_KEY = 'zpt_cart'
SESSION_CART_MODE_KEY = 'zpt_cart_mode'
SESSION_UTM_KEY = 'zpt_order_utm'
SESSION_WHOLESALE_VISITOR_KEY = 'wholesale_visitor_id'

CART_MODE_RETAIL = 'retail'
CART_MODE_WHOLESALE = 'wholesale'

CART_MODE_CONFLICT = 'cart_mode_conflict'

UTM_SOURCE_MAX_LENGTH = 100
UTM_MEDIUM_MAX_LENGTH = 100
UTM_CAMPAIGN_MAX_LENGTH = 150

TRANSPORT_COMPANIES = [
    ('cdek', 'CDEK'),
    ('kazpost', 'Казпочта'),
]

DEFAULT_WAREHOUSE_ADDRESS = 'г. Алматы, ул. Мурат, 94А'
