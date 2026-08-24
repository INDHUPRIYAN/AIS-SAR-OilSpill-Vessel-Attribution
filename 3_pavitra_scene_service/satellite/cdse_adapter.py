import requests

class CDSEAdapter:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.token = None

    def refresh_token(self):
        # Authenticates with CDSE Keycloak/OAuth server
        pass

    def search_scenes(self, bbox, start_time, end_time):
        # Queries OData catalog endpoint
        return []
