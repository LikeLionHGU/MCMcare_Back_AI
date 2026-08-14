#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
광은님 Spring 서버(MCMcare_Back)에서 AS 데이터를 가져온다.

[왜 필요한가]
  챗봇은 지금 as_dummy.json(더미 파일)에서 AS 접수 건을 읽는다.
  실제 서비스에서는 그 데이터가 Spring + MySQL에 있다.
  언어가 달라서(Java vs Python) 한 프로세스로 못 합치므로, HTTP로 가져온다.

[설계]
  1. 환경변수 SPRING_API_URL이 없으면 이 모듈은 아무것도 하지 않는다.
     → 지금까지처럼 더미 파일로 동작한다. (기존 동작을 절대 깨지 않는다)
  2. 서버 호출이 실패하면 예외를 던지지 않고 None을 돌려준다.
     → 챗봇이 더미로 되돌아간다. 시연 중 서버가 죽어도 챗봇은 산다.
  3. Spring의 날짜(LocalDate "2026-07-23")를 챗봇이 쓰는
     "2026년 07월 23일" 문자열로 바꾼다. 안 바꾸면 GPT 답변이 어색해진다.

[환경변수]
  SPRING_API_URL    예: http://localhost:8080   (없으면 비활성)
  SPRING_API_TOKEN  JWT가 필요하면 설정. Authorization: Bearer <값>
  SPRING_TIMEOUT    응답 대기 초 (기본 3)

[사용]
  from spring_client import fetch_repair, fetch_estimate, is_enabled
"""

import os
import logging

log = logging.getLogger("spring_client")

try:
    import requests
except ImportError:                      # requests가 없어도 챗봇은 돌아야 한다
    requests = None

BASE_URL = os.getenv("SPRING_API_URL", "").strip().rstrip("/")
TOKEN = os.getenv("SPRING_API_TOKEN", "").strip()
TIMEOUT = float(os.getenv("SPRING_TIMEOUT", "3"))


def is_enabled():
    """Spring 연동이 켜져 있는지. 꺼져 있으면 챗봇은 더미 파일을 쓴다."""
    return bool(BASE_URL) and requests is not None


def _headers():
    h = {"Accept": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def _get(path):
    """GET 한 번. 실패는 로그만 남기고 None을 돌려준다 (챗봇을 죽이지 않는다)."""
    if not is_enabled():
        return None
    url = f"{BASE_URL}{path}"
    try:
        r = requests.get(url, headers=_headers(), timeout=TIMEOUT)
    except Exception as e:
        log.warning("[Spring] 접속 실패 %s — %s", url, e)
        return None

    if r.status_code == 401 or r.status_code == 403:
        log.warning("[Spring] 인증 실패 (%s). SPRING_API_TOKEN을 확인하세요.", r.status_code)
        return None
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        log.warning("[Spring] %s → %s", url, r.status_code)
        return None
    try:
        return r.json()
    except Exception:
        log.warning("[Spring] JSON 파싱 실패: %s", url)
        return None


# ─────────────────────────────────────────────────────────────
# 날짜 변환
#   Spring은 LocalDate를 "2026-07-23", LocalDateTime을 "2026-07-23T14:05:00"으로 준다.
#   챗봇 프롬프트에는 "2026년 07월 23일" 형태로 들어가야 자연스럽다.
# ─────────────────────────────────────────────────────────────

def _kdate(value):
    if not value:
        return None
    s = str(value)[:10]                   # 시각 부분은 버린다
    parts = s.split("-")
    if len(parts) != 3:
        return s                          # 형식이 다르면 원본 그대로 (추측하지 않는다)
    y, m, d = parts
    return f"{y}년 {m}월 {d}일"


# ─────────────────────────────────────────────────────────────
# AS 상세  GET /api/asCase/detail/{asNo}   (DetailResDto)
# ─────────────────────────────────────────────────────────────

def fetch_repair(as_id):
    """
    접수번호 하나를 Spring에서 가져와 챗봇이 쓰는 형태로 바꾼다.
    실패하거나 연동이 꺼져 있으면 None → 호출한 쪽이 더미로 되돌아간다.

    ⚠️ DetailResDto에는 product_code(상품코드)가 없다.
       챗봇은 상품코드로 mcm_products.csv의 소재·치수를 찾으므로,
       코드가 없으면 '이 고객의 제품 스펙'까지는 답하지 못한다.
       광은님께 modelCode 필드 추가를 요청해 둔 상태.
    """
    data = _get(f"/api/asCase/detail/{as_id}")
    if not data:
        return None

    history = data.get("historyList") or []
    timeline = [{
        "status": h.get("status"),
        "step":   h.get("statusLabel") or h.get("status"),
        "date":   _kdate(h.get("occurredAt")),
        "done":   bool(h.get("completed")),
        "note":   h.get("description") or "",
    } for h in history]

    return {
        "as_id":           data.get("asNo") or as_id,
        "product_code":    data.get("modelCode") or "",     # 아직 없을 수 있음
        "product_name":    data.get("modelName") or "",
        "color":           data.get("color") or "",
        "damage_type":     data.get("damageCategory") or data.get("damageType") or "",
        "damage_part":     data.get("damagePart") or "",
        "damage_description": data.get("damageDescription") or "",
        "intake_type":     data.get("intakeType") or "",
        "status":          data.get("status") or "",
        "stage":           data.get("statusLabel") or data.get("status") or "",
        "status_message":  data.get("statusMessage") or "",
        "received_at":     _kdate(data.get("createdAt")),
        "expected_at":     _kdate(data.get("expectedCompletedAt")),
        "updated_at":      _kdate(data.get("statusUpdatedAt")),
        "delay_reason":    data.get("delayReason") or "",
        "location":        data.get("currentLocation") or "",
        "location_status": data.get("locationStatus") or "",
        "tracking_number": data.get("trackingNumber"),
        "pickup_no":       data.get("pickupNo"),
        "timeline":        timeline,
        "_source":         "spring",
    }


# ─────────────────────────────────────────────────────────────
# AI 예상 견적  GET /api/asCase/estimate/{asNo}   (EstimateResDto)
# ─────────────────────────────────────────────────────────────

def fetch_estimate(as_id):
    """
    견적 결과를 '지식 항목' 형태로 바꿔 돌려준다. 없으면 None.

    이게 있으면 챗봇이 금액을 근거를 갖고 말할 수 있다.
    (지금은 자료가 없어서 'AI 예상 견적 받기'로 유도만 한다)
    """
    data = _get(f"/api/asCase/estimate/{as_id}")
    if not data:
        return None

    items = data.get("itemList") or []
    if not items and data.get("totalMinPrice") is None:
        return None                        # 아직 분석 전이면 지식으로 넣지 않는다

    lines = [
        f"이 고객의 AS 접수 {data.get('asNo', as_id)} 건에 대한 AI 예상 견적 결과다.",
        "이 금액은 확정가가 아니라 예상 범위이며, 실물 진단 후 최종 확정된다.",
    ]
    if data.get("damageCategory"):
        lines.append(f"분류된 손상 유형: {data['damageCategory']}")
    if data.get("damageSeverity"):
        lines.append(f"손상 정도: {data['damageSeverity']}")
    if data.get("confidenceGrade"):
        note = data.get("confidenceNote") or ""
        lines.append(f"분석 신뢰도: {data['confidenceGrade']} {note}".strip())

    if items:
        lines.append("수선 항목별 예상 비용:")
        for it in items:
            lines.append(
                f"- {it.get('repairItemName', '')}: "
                f"{int(it.get('minPrice', 0)):,}원 ~ {int(it.get('maxPrice', 0)):,}원"
            )
    if data.get("totalMinPrice") is not None:
        lines.append(
            f"예상 합계: {int(data['totalMinPrice']):,}원 ~ "
            f"{int(data.get('totalMaxPrice', 0)):,}원"
        )

    # 보증 판정 — 무상/유상이 갈리므로 금액만큼 중요하다
    if data.get("warrantyVerdictLabel"):
        lines.append(f"보증 판정: {data['warrantyVerdictLabel']}")
    for n in (data.get("warrantyNoteList") or []):
        lines.append(f"- {n}")

    lines.append(
        "금액을 물으면 위 범위를 그대로 안내하고, "
        "확정가가 아님을 반드시 함께 말한다. 범위를 벗어난 숫자는 절대 말하지 않는다."
    )

    return {
        "topic": f"AI 예상 견적 {data.get('asNo', as_id)}",
        "keywords": [],                    # 검색이 아니라 항상 앞에 붙인다
        "source": "AI 예상 견적 (서버 분석 결과)",
        "content": "\n".join(lines),
    }


if __name__ == "__main__":
    # 연결 확인용:  SPRING_API_URL=http://localhost:8080 python spring_client.py AS-2026-00871
    import sys, json
    logging.basicConfig(level=logging.INFO)
    if not is_enabled():
        print("SPRING_API_URL이 설정되지 않았습니다. 더미 모드로 동작합니다.")
        sys.exit(0)
    target = sys.argv[1] if len(sys.argv) > 1 else "AS-2026-00871"
    print(f"[{BASE_URL}] {target} 조회")
    print("--- 상세 ---")
    print(json.dumps(fetch_repair(target), ensure_ascii=False, indent=2))
    print("--- 견적 ---")
    print(json.dumps(fetch_estimate(target), ensure_ascii=False, indent=2))
