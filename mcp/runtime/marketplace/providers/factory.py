"""One factory; provider selection is an internal implementation detail."""
from schema import identity,order_identity,SITES

def create_provider(name):
    if name=='ozon':
        from providers.ozon import OzonProvider
        return OzonProvider()
    if name=='aliexpress':
        from providers.aliexpress import AliExpressProvider
        return AliExpressProvider()
    raise ValueError('unknown_provider')

def for_product(ref):return create_provider(identity(ref)[0])
def for_order(ref):return create_provider(order_identity(ref)[0])
def all_providers():return [create_provider(name) for name in SITES]
