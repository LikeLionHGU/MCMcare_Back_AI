# 가비아처럼 직접 서버를 받는 경우용.
# Render/Railway를 쓰면 이 파일은 필요 없다.
FROM python:3.11-slim

WORKDIR /app

# 의존성을 먼저 복사해야 코드만 바뀔 때 캐시가 살아 빌드가 빨라진다
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 컨테이너 안에서는 8000 고정. 외부 포트는 실행할 때 매핑한다.
EXPOSE 8001
CMD ["uvicorn", "as_chatbot:app", "--host", "0.0.0.0", "--port", "8001"]
