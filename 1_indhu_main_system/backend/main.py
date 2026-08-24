import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="oceantrace Main API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "main_system"}

@app.post("/detect")
def detect_oil_spill(scene_id: str):
    # Triggers DARTIS / Trujillo classifier detection
    return {
        "scene_id": scene_id,
        "spills_detected": [],
        "fallback_active": True
    }

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
