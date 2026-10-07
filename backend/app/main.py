from fastapi import FastAPI

app = FastAPI(title="MapGPT")

@app.get("/")
def read_root():
    return {"message": "서버 실행 중"}