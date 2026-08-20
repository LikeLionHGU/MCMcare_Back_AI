#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
광은님 Spring 서버(MCMcare_Back) 흉내내기 — 연동 테스트용 가짜 서버.

[왜 필요한가]
  spring_client.py는 광은님 서버가 떠 있어야 검증할 수 있다.
  그런데 서버 주소를 아직 못 받았다. 그렇다고 "붙여봐야 안다"로 두면
  통합하는 날 처음 문제를 발견하게 된다. (마감 이틀 전에)
  그래서 실제 DTO 필드명·날짜 형식 그대로 돌려주는 가짜 서버를 만들어
  매핑이 맞는지 지금 확인한다.

  응답 필드는 MCMcare_Back의 AsCaseDto.DetailResDto / EstimateResDto와
  AsStatus enum을 그대로 따랐다. (커밋 ef7b74e 기준)

[실행]
  pip install fastapi uvicorn
  python mock_spring.py                 # http://127.0.0.1:8080 에서 뜬다

[챗봇을 여기에 붙여보기]
  터미널 1:  python mock_spring.py
  터미널 2:  SPRING_API_URL=http://127.0.0.1:8080 MCM_MOCK=1 \
             uvicorn as_chatbot:app --port 8000
             → http://127.0.0.1:8000/?as_id=AS-2026-00871
"""

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="MOCK MCMcare_Back", version="mock-0.1")

# Spring은 LocalDate를 "2026-07-23", LocalDateTime을 "2026-07-23T14:05:00"로 준다.
# 챗봇이 이걸 "2026년 07월 23일"로 바꿔 쓰는지 확인하는 게 이 파일의 목적이다.
DETAIL = {
    "AS-2026-00871": {
        "asNo": "AS-2026-00871",
        "modelName": "Stark 사이드 스터드 비세토스 백팩",
        "createdAt": "2026-07-23",
        "intakeType": "픽업 수거 접수",
        "pickupNo": "PKP-2026-000847",
        "status": "REPAIRING",
        "statusLabel": "수선 진행 중",
        "statusUpdatedAt": "2026-08-11T14:05:00",
        "statusMessage": "수선 센터에서 하드웨어 교체 작업 중입니다.",
        "expectedCompletedAt": "2026-08-22",
        "expectedUpdatedAt": "2026-08-11",
        "currentLocation": "MCM 서울 수선 센터",
        "locationType": "REPAIR_CENTER",
        "locationStatus": "수선 작업 중",
        "damagePart": "메인 수납부 지퍼",
        "historyList": [
            {"status": "PICKED_UP",  "statusLabel": "수거 완료",
             "completed": True,  "occurredAt": "2026-07-24",
             "description": "기사 인계 후 수선 센터로 이동"},
            {"status": "RECEIVED",   "statusLabel": "접수 완료",
             "completed": True,  "occurredAt": "2026-07-26",
             "description": "수선 센터 입고 및 접수 처리"},
            {"status": "DIAGNOSED",  "statusLabel": "진단 및 견적 확정",
             "completed": True,  "occurredAt": "2026-07-28",
             "description": "실물 진단 후 수선 범위 확정"},
            {"status": "REPAIRING",  "statusLabel": "수선 진행 중",
             "completed": True,  "occurredAt": "2026-07-29",
             "description": "확정된 범위로 수선 작업 진행"},
            # 아직 안 온 단계는 Jackson의 non_null 설정 때문에
            # occurredAt 키가 통째로 빠진다. 그 상황을 그대로 재현한다.
            {"status": "INSPECTING", "statusLabel": "품질 검수",
             "completed": False, "description": "수선 완료 후 품질 기준 최종 점검"},
            {"status": "SHIPPING",   "statusLabel": "반환 배송",
             "completed": False, "description": "검수 완료 후 고객 배송 진행"},
            {"status": "COMPLETED",  "statusLabel": "완료",
             "completed": False, "description": "수선 완료 및 인계 종료"},
        ],
    }
}

ESTIMATE = {
    "AS-2026-00871": {
        "asNo": "AS-2026-00871",
        "status": "ESTIMATED",
        "statusLabel": "견적 안내 완료",
        "modelName": "Stark 사이드 스터드 비세토스 백팩",
        "damagePart": "메인 수납부 지퍼",
        "photoUrlList": ["http://localhost:8080/files/demo1.jpg"],
        "damageCategory": "금속부품손상",
        "damageSeverity": "중간 — 부분 수선 가능 수준",
        "confidenceGrade": "높음",
        "confidenceNote": "제출 사진 2장 기반",
        "itemList": [
            {"repairItemName": "하드웨어 교체", "minPrice": 80000, "maxPrice": 120000}
        ],
        "totalMinPrice": 80000,
        "totalMaxPrice": 120000,
        "purchasedAt": "2024-05-11",
        "warrantyMonths": 24,
        "warrantyScope": "제조상 하자",
        "warrantyVerdict": "OUT_OF_WARRANTY",
        "warrantyVerdictLabel": "보증 기간 경과 — 유상 수선",
        "warrantyNoteList": ["구매 후 24개월이 지나 무상 보증 대상이 아닙니다."],
    }
}


@app.get("/api/asCase/detail/{as_no}")
def detail(as_no: str):
    d = DETAIL.get(as_no.upper())
    if not d:
        return JSONResponse({"code": "AS_NOT_FOUND"}, status_code=404)
    return d


@app.get("/api/asCase/estimate/{as_no}")
def estimate(as_no: str):
    e = ESTIMATE.get(as_no.upper())
    if not e:
        return JSONResponse({"code": "ESTIMATE_NOT_FOUND"}, status_code=404)
    return e


@app.get("/health")
def health():
    return {"status": "ok", "mock": True}


if __name__ == "__main__":
    print("MOCK Spring 서버: http://127.0.0.1:8080")
    print("  GET /api/asCase/detail/AS-2026-00871")
    print("  GET /api/asCase/estimate/AS-2026-00871")
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="warning")
