"""Shared supplier files outlive individual product image assignments."""


def clear_main_image(product):
    if product.main_image and not product.main_image.name.startswith('products/optimized/'):
        product.main_image.delete(save=False)
    product.main_image = None
    product.supplier_thumbnail = ''
