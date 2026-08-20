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
import time
import hashlib
import logging
from contextvars import ContextVar

log = logging.getLogger("spring_client")

# 요청마다 다른 JWT를 쓰기 위한 자리.
#
# [왜 필요한가]
# 광은님 서버는 Spring Security + JWT 라서 "누가 보냈는지"를 토큰으로 판별한다.
# 고정 토큰 하나를 환경변수에 박아두면 모든 고객이 같은 사람으로 취급된다.
# 프론트가 챗봇을 부를 때 고객의 Authorization 헤더를 그대로 넘겨주면
# 미들웨어가 여기에 담고, 그 턴의 Spring 호출에만 쓰인다.
_request_token = ContextVar("spring_request_token", default="")


# 마지막 조회가 왜 실패했는지. 요청마다 따로 관리한다.
#   ""          성공했거나 아직 안 불렀다
#   "not_found" 서버는 살아 있는데 그런 건이 없다 (404) 또는 남의 건이다 (403)
#   "error"     서버에 못 닿았다 (타임아웃·5xx·파싱 실패)
#
# [왜 필요한가]
# 둘을 구분하지 못하면 "없는 접수번호"와 "서버가 죽음"에 같은 답을 하게 된다.
# 고객에게 할 말이 다르다.
_last_failure = ContextVar("spring_last_failure", default="")


def last_failure():
    return _last_failure.get()


def current_token():
    """이번 요청에 실려 온 고객 토큰. 없으면 빈 문자열."""
    return _request_token.get() or ""


def set_request_token(value):
    """FastAPI 미들웨어가 매 요청 앞에서 호출한다. 'Bearer xxx' 통째로 받아도 된다."""
    v = (value or "").strip()
    if v.lower().startswith("bearer "):
        v = v[7:].strip()
    _request_token.set(v)
    return v

try:
    import requests
except ImportError:                      # requests가 없어도 챗봇은 돌아야 한다
    requests = None

BASE_URL = os.getenv("SPRING_API_URL", "").strip().rstrip("/")
TOKEN = os.getenv("SPRING_API_TOKEN", "").strip()
TIMEOUT = float(os.getenv("SPRING_TIMEOUT", "3"))
CACHE_TTL = float(os.getenv("SPRING_CACHE_TTL", "30"))

# 짧은 캐시.
#
# [왜 필요한가]
# 챗봇은 고객이 말을 걸 때마다 접수 상세와 견적을 각각 한 번씩 불러온다.
# 대화 한 턴에 HTTP 왕복이 2번씩 붙는 셈이라, 서버가 느리면 그만큼 답이 늦어지고
# 서버가 죽어 있으면 타임아웃(기본 3초)을 매 턴 두 번씩 기다린다.
# AS 진행 상태는 대화 중에 바뀌지 않으므로 짧게 캐시해도 안전하다.
# 실패(None)도 캐시한다 — 죽은 서버를 매 턴 다시 찌를 이유가 없다.
_cache = {}


def _scoped(key):
    """
    캐시 키에 '누구의 토큰인지'를 섞는다.

    왜: 토큰이 요청마다 다른데 캐시 키가 접수번호뿐이면,
        A 고객이 받은 응답을 B 고객이 그대로 받아볼 수 있다. 개인정보 사고다.
    토큰 원문을 키에 넣지 않고 해시 앞 12자만 쓴다 (로그·메모리 노출 최소화).
    """
    token = _request_token.get() or TOKEN
    if not token:
        return key
    return f"{hashlib.sha256(token.encode()).hexdigest()[:12]}:{key}"


def _cache_get(key):
    """(적중 여부, 값). 만료됐거나 없으면 (False, None)."""
    hit = _cache.get(key)
    if not hit:
        return False, None
    value, expires_at = hit
    if time.time() > expires_at:
        _cache.pop(key, None)
        return False, None
    return True, value


def _cache_put(key, value):
    _cache[key] = (value, time.time() + CACHE_TTL)
    if len(_cache) > 200:                 # 메모리가 무한정 늘지 않게
        oldest = min(_cache, key=lambda k: _cache[k][1])
        _cache.pop(oldest, None)


def clear_cache():
    """대화 초기화 등 '지금 최신 값을 다시 보고 싶을 때' 쓴다."""
    _cache.clear()


def is_enabled():
    """Spring 연동이 켜져 있는지. 꺼져 있으면 챗봇은 더미 파일을 쓴다."""
    return bool(BASE_URL) and requests is not None


def _headers():
    # 고객 토큰이 있으면 그것을 우선한다. 없으면 환경변수 토큰(데모·서버간 호출용).
    token = _request_token.get() or TOKEN
    h = {"Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get(path):
    """GET 한 번. 실패는 로그만 남기고 None을 돌려준다 (챗봇을 죽이지 않는다)."""
    if not is_enabled():
        _last_failure.set("")
        return None
    url = f"{BASE_URL}{path}"
    try:
        r = requests.get(url, headers=_headers(), timeout=TIMEOUT)
    except Exception as e:
        log.warning("[Spring] 접속 실패 %s — %s", url, e)
        _last_failure.set("error")
        return None

    if r.status_code in (401, 403):
        # 남의 접수 건을 조회했거나 토큰이 만료됐다. 서버는 정상이다.
        log.warning("[Spring] 권한 없음 (%s) %s", r.status_code, url)
        _last_failure.set("not_found")
        return None
    if r.status_code == 404:
        _last_failure.set("not_found")
        return None
    if r.status_code >= 400:
        log.warning("[Spring] %s → %s", url, r.status_code)
        _last_failure.set("error")
        return None
    try:
        data = r.json()
        _last_failure.set("")
        return data
    except Exception:
        log.warning("[Spring] JSON 파싱 실패: %s", url)
        _last_failure.set("error")
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
    cached, value = _cache_get(_scoped(f"detail:{as_id}"))
    if cached:
        return value

    data = _get(f"/api/asCase/detail/{as_id}")
    if not data:
        _cache_put(_scoped(f"detail:{as_id}"), None)
        return None

    history = data.get("historyList") or []
    timeline = [{
        "status": h.get("status"),
        "step":   h.get("statusLabel") or h.get("status"),
        "date":   _kdate(h.get("occurredAt")),
        "done":   bool(h.get("completed")),
        "note":   h.get("description") or "",
    } for h in history]

    result = {
        "as_id":           data.get("asNo") or as_id,
        "product_code":    data.get("modelCode") or "",     # 아직 없을 수 있음
        "product_name":    data.get("modelName") or "",
        "color":           data.get("color") or "",
        # 손상 정보는 두 종류다 (API 명세서 3-6).
        #   고객이 접수 시 고른 것  damageTypeLabel / damageType  ("봉제손상")
        #   AI 가 사진을 보고 판정  damageCategory                ("손상(세부미상)")
        # 둘이 다를 수 있고, 다른 것 자체가 상담에 쓸 정보다.
        # 인사말·본문에는 고객이 고른 값을 쓴다 — AI 클래스명은 손님이 알 필요 없는 말이다.
        "damage_type":     (data.get("damageTypeLabel")
                            or data.get("damageType") or ""),
        "damage_category": data.get("damageCategory") or "",
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
    _cache_put(_scoped(f"detail:{as_id}"), result)
    return result


# ─────────────────────────────────────────────────────────────
# AI 예상 견적  GET /api/asCase/estimate/{asNo}   (EstimateResDto)
# ─────────────────────────────────────────────────────────────

def fetch_estimate(as_id):
    """
    견적 결과를 '지식 항목' 형태로 바꿔 돌려준다. 없으면 None.

    이게 있으면 챗봇이 금액을 근거를 갖고 말할 수 있다.
    (지금은 자료가 없어서 'AI 예상 견적 받기'로 유도만 한다)
    """
    cached, value = _cache_get(_scoped(f"estimate:{as_id}"))
    if cached:
        return value

    data = _get(f"/api/asCase/estimate/{as_id}")
    if not data:
        _cache_put(_scoped(f"estimate:{as_id}"), None)
        return None

    items = data.get("itemList") or []
    if not items and data.get("totalMinPrice") is None:
        _cache_put(_scoped(f"estimate:{as_id}"), None)
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

    result = {
        "topic": f"AI 예상 견적 {data.get('asNo', as_id)}",
        "keywords": [],                    # 검색이 아니라 항상 앞에 붙인다
        "source": "AI 예상 견적 (서버 분석 결과)",
        "content": "\n".join(lines),
    }
    _cache_put(_scoped(f"estimate:{as_id}"), result)
    return result


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


# ─────────────────────────────────────────────────────────────
# 로그인한 회원  GET /api/member/info   (MemberDto.InfoResDto)
# ─────────────────────────────────────────────────────────────

def fetch_member():
    """
    지금 요청을 보낸 고객이 누구인지 가져온다. 실패하면 None.

    [왜 필요한가]
    DetailResDto 에는 고객 이름이 없다. 그래서 챗봇이 as_dummy.json 의
    고객(김민서)을 전역으로 들고 응대하고 있었다.
    로그인이 달라도 계속 "김민서 고객님"이라고 부른다 — 남의 이름이다.

    토큰으로 조회하므로 자기 자신만 나온다. 이름 외의 값(이메일·전화·생일)은
    상담에 필요 없고 유출 위험만 있으므로 담지 않는다.
    """
    hit, value = _cache_get(_scoped("member"))
    if hit:
        return value

    data = _get("/api/member/info")
    result = None
    if isinstance(data, dict):
        name = (data.get("name") or "").strip()
        if name:
            result = {"name": name}
    _cache_put(_scoped("member"), result)
    return result
