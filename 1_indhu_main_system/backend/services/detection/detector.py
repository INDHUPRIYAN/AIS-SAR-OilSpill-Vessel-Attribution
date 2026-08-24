class OilSpillDetector:
    def __init__(self, model_path=None):
        self.model_path = model_path

    def predict(self, image_data):
        # Fallback threshold detection if ML model is unavailable
        return {"detected": False, "confidence": 0.0}
