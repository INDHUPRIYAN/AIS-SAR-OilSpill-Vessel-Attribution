"""Local pytest configuration for scene_service tests."""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: integration tests against the real Sentinel-1 scene on disk "
        "(skipped automatically when the scene is absent)",
    )
