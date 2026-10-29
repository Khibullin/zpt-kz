from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Offer,KaspiSellerSnapshot,RetailListing


@receiver(post_save,sender=Offer)
@receiver(post_save,sender=KaspiSellerSnapshot)
def sync_retail(sender,instance,raw=False,**kwargs):
    if raw:return
    offer_id=instance.pk if sender is Offer else instance.offer_id
    item=RetailListing.objects.select_related('offer','product').filter(offer_id=offer_id).first()
    if item:
        from .retail import refresh_listing
        refresh_listing(item)
