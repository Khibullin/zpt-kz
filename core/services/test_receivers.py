"""Explicit owner-authorized test scope. Never grants privileges to ordinary sellers."""
from django.db.models import Q


def test_receiver_decision(seller, req):
    """None: ordinary routing; True: universal new request; False: historical request."""
    since = getattr(seller, 'test_all_requests_since', None)
    if not getattr(seller, 'is_test_seller', False) or since is None or req is None:
        return None
    created = getattr(req, 'created_at', None)
    return bool(created is not None and created >= since)


def universal_test_q(req):
    created = getattr(req, 'created_at', None)
    if created is None:
        return Q(pk__in=[])
    return Q(is_test_seller=True, test_all_requests_since__lte=created)
