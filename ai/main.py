from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import routers
from sentiment import app as sentiment_router
from predict import router as prediction_router
from query_analyzer import router as query_router

app = FastAPI(title="TradeMind AI Service")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(sentiment_router, prefix="/sentiment", tags=["Sentiment Analysis"])
app.include_router(prediction_router, prefix="/prediction", tags=["Price Prediction"])
app.include_router(query_router, prefix="/query", tags=["Query Analysis"])

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)