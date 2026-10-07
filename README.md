### 1. Requirements
- Python 3.12.8

### 2. Setup (venv)
```bash
# 가상환경 생성
python -m venv venv

# 가상환경 활성화 (macOS / Linux)
source venv/bin/activate

# 가상환경 활성화 (Windows CMD)
venv\Scripts\activate.bat

# 가상환경 활성화 (Windows PowerShell)
venv\Scripts\Activate.ps1

# 패키지 설치
pip install -r requirements.txt

### 서버 실행
uvicorn app.main:app --reload


