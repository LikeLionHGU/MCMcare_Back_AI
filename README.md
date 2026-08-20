# CUSTODIA — MCMcare AI 백엔드

AI를 통해 고객과 럭셔리 브랜드를 잇는 AS 통합 플랫폼

> Spring 백엔드 레포와 프론트 레포는 별도로 관리됩니다.

---

## 폴더 구조

### `chatbot/` — AS 상담 챗봇 (임규민)
FastAPI + RAG 기반 챗봇. 사용자의 AS 문의를 받아 MCM 제품 정보 및 AS 접수 데이터를 바탕으로 답변을 생성합니다.

**실행 방법**
```bash
cd chatbot
pip install -r requirements.txt
uvicorn as_chatbot:app --reload
```

### `ai-model/` — 손상 진단 AI (장동원)
FastAPI + YOLOv8 기반 이미지 진단 모델. 제품 사진을 분석하여 손상 부위와 수리 비용을 추정합니다.

**실행 방법**
```bash
cd ai-model/03_api
pip install -r requirements.txt
uvicorn main:app --reload
```

---

## 배포

`render.yaml` 에 두 서비스가 정의되어 있습니다. Render 대시보드에서 연결하면 자동 배포됩니다.
