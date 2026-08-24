class MetoceanChain:
    def __init__(self, adapters):
        self.adapters = adapters

    def fetch_metocean(self, bbox, time_range):
        # GLORYS -> ERA5 -> HYCOM -> OpenMeteo -> StaticCache routing fallback
        pass
