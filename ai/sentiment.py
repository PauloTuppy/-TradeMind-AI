import os
from openai import OpenAI
from fastapi import FastAPI, HTTPException

app = FastAPI()
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

@app.post("/analyze-sentiment")
async def analyze_sentiment(text: str):
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": f"Analyze crypto market sentiment in this text (bullish/bearish/neutral with confidence score): {text}"}]
        )
        return {"sentiment": response.choices[0].message.content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))