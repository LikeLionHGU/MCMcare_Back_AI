#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCM AS 챗봇 — OpenAI 연동 부분만 발췌 (as_chatbot.py에서)

전체 코드가 아니라 OpenAI를 호출하는 부분만 뽑은 것입니다.
결제·조직 설정 확인용 참고 자료입니다. 이 파일 단독으로는 실행되지 않습니다.

[설정 요약]
  모델        gpt-4o-mini
  조직        org-0mvev2qGfXIZ2DiNJRrguYmH  (사다리타기)
  키 위치     .env 파일의 OPENAI_API_KEY   (Git 제외)
  temperature 0.2
  max_tokens  400
  timeout     30초
  요청당 입력 토큰  평균 400~1,200

[현재 문제]
  무료 티어 한도 — 하루 50회 / 분당 10회
  결제 수단 등록 시 하루 10,000회 / 분당 500회로 상향
  지금까지 사용량: 62회 호출에 $0.01 (약 15원)
"""

import os
import re
import time

MODEL = "gpt-4o-mini"
HISTORY_TURNS = 3          # GPT에 함께 넘길 직전 대화 수
RETRY_MAX_WAIT = 20        # 이보다 오래 기다려야 하면 재시도 포기


# ─────────────────────────────────────────────────────────────
# 1. 설정 읽기 (.env 파일)
#
#    API 키를 코드에 직접 적으면 Git에 그대로 올라간다.
#    GitHub은 이걸 감지해서 OpenAI에 알리고 키가 자동 폐기된다.
#    그래서 키는 .env에 두고 그 파일은 .gitignore로 제외한다.
#
#    터미널에서 export 한 값이 있으면 그쪽이 우선이다. (override=False)
# ─────────────────────────────────────────────────────────────

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

try:
    from dotenv import load_dotenv
    if load_dotenv(_ENV_PATH, override=False):
        print(f"[설정] .env 파일을 읽었습니다. ({_ENV_PATH})")
except ImportError:
    if os.path.exists(_ENV_PATH):
        print("[안내] .env는 있지만 python-dotenv가 없습니다. pip install python-dotenv")


# ─────────────────────────────────────────────────────────────
# 2. 개인정보 마스킹 — GPT로 보내기 전에 지운다
#
#    프롬프트로 "말하지 마"라고 부탁하는 건 방어가 아니다.
#    아예 문자열에서 지워서 구조적으로 차단한다.
#    순서가 중요하다. 긴 것(카드·주민번호)을 먼저 지워야
#    짧은 전화번호 규칙이 카드번호 가운데를 잘라먹지 않는다.
# ─────────────────────────────────────────────────────────────

PII_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[이메일]"),
    (re.compile(r"(?<!\d)(?:\d{4}[-\s]?){3}\d{4}(?!\d)"), "[카드번호]"),
    (re.compile(r"(?<!\d)\d{6}[-\s]?[1-4]\d{6}(?!\d)"), "[주민번호]"),
    (re.compile(r"(?<!\d)01[016-9][-\s.]?\d{3,4}[-\s.]?\d{4}(?!\d)"), "[전화번호]"),
    (re.compile(r"(?<!\d)0\d{1,2}[-\s]\d{3,4}[-\s]\d{4}(?!\d)"), "[전화번호]"),
    (re.compile(r"(?<!\d)\d{5}(?=\s*(?:번지|동|호|아파트|로|길))"), "[주소]"),
]


def mask_pii(text):
    """개인정보로 보이는 부분을 치환한다. 반환: (치환된 텍스트, 걸린 개수)"""
    hit = 0
    for pattern, tag in PII_PATTERNS:
        text, n = pattern.subn(tag, text)
        hit += n
    return text, hit


def redact(text):
    """에러 로그에 API 키가 찍히지 않게 가린다. 로그는 키 유출의 흔한 경로다."""
    return re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-***REDACTED***", text)


# ─────────────────────────────────────────────────────────────
# 3. 프롬프트 — 이 규칙으로만 답하게 한다 (일부 발췌)
# ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """당신은 MCM 케어의 AS 상담 직원입니다.

[내용 규칙]
1. 아래 '참고 자료'에 있는 내용만으로 답하세요. 없는 내용은 만들어내지 마세요.
2. 답할 수 없을 때는 무엇을 하면 되는지 알려주세요.
3. 수선 비용을 숫자로 말하지 마세요. 'AI 예상 견적 받기'로 안내하세요.
4. 다른 고객의 정보는 어떤 경우에도 언급하지 마세요.

[말투 규칙]
5. "제가 가진 자료로", "총 N건" 같은 내부 표현 금지.
6. 결론부터, 1~3문장.
7. 내용 없는 맺음말("그렇게 해보시면 좋을 것 같아요") 금지.
"""


# ─────────────────────────────────────────────────────────────
# 4. 호출 준비 — 키 검증 + 메시지 구성
# ─────────────────────────────────────────────────────────────

def _prepare(question, context, history):
    """
    반환: (client, messages, None)  또는  (None, None, 오류메시지)
    """
    try:
        from openai import OpenAI
    except ImportError:
        return None, None, "[오류] openai 패키지가 없습니다. pip install openai"

    # 키를 복사할 때 앞뒤 공백·줄바꿈이 딸려오는 일이 잦다.
    # 그대로 두면 HTTP 헤더가 깨져 LocalProtocolError가 나는데,
    # 겉으로는 "Connection error"로 보여 네트워크 문제로 착각하게 된다.
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()

    if not api_key:
        return None, None, "[오류] OPENAI_API_KEY가 없습니다. .env를 만드세요."
    if "여기에" in api_key or "실제" in api_key:
        return None, None, "[오류] .env에 예시 문구가 그대로 있습니다."
    if not api_key.startswith("sk-"):
        return None, None, "[오류] 'sk-'로 시작해야 합니다."
    if any(c.isspace() for c in api_key) or not api_key.isascii():
        return None, None, (f"[오류] 키에 공백·줄바꿈 또는 비ASCII 문자가 있습니다. "
                            f"(길이 {len(api_key)})")

    # 질문에서 개인정보를 지운 뒤 자료와 함께 묶는다
    safe_question, _ = mask_pii(question)
    user_msg = f"[참고 자료]\n{context}\n\n[고객 질문]\n{safe_question}"

    # 이전 대화도 마스킹해서 넘긴다.
    # 앞 턴에 적힌 전화번호가 뒤 턴에서 다시 새어나가는 걸 막는다.
    prior = []
    for m in (history or [])[-HISTORY_TURNS * 2:]:
        content = mask_pii(str(m.get("content", "")))[0]
        if content:
            prior.append({"role": m.get("role", "user"), "content": content})

    # 환경변수에 의존하지 않고 정리된 키를 직접 넘긴다
    client = OpenAI(api_key=api_key, timeout=30.0)
    messages = ([{"role": "system", "content": SYSTEM_PROMPT}]
                + prior + [{"role": "user", "content": user_msg}])
    return client, messages, None


# ─────────────────────────────────────────────────────────────
# 5. 429(한도 초과) 판단
# ─────────────────────────────────────────────────────────────

QUOTA_EXHAUSTED = {"hit": False, "message": ""}


def _retry_after(e):
    """
    429일 때 몇 초 뒤 재시도하면 되는지 알아낸다.
    분당 한도(RPM)는 몇 초면 풀리지만, 하루 한도(RPD)는 수십 분이라 의미가 없다.
    """
    msg = str(e)
    if not any(k in msg for k in ("429", "rate_limit", "Rate limit", "RateLimit")):
        return None

    # "28m48s" / "6s" / "500ms" 모두 처리
    m = re.search(r"try again in\s*(?:(\d+)m)?\s*([\d.]+)\s*(ms|s)\b", msg)
    if not m:
        return None

    minutes = int(m.group(1)) if m.group(1) else 0
    val, unit = float(m.group(2)), m.group(3)
    sec = minutes * 60 + (val / 1000 if unit == "ms" else val)

    if sec > RETRY_MAX_WAIT:
        QUOTA_EXHAUSTED["hit"] = True
        QUOTA_EXHAUSTED["message"] = f"약 {sec / 60:.0f}분 뒤 풀립니다"
        return None
    return sec + 1


def _log_error(e):
    """겉껍데기 예외와 진짜 원인을 모두 남긴다. API 키는 가린다."""
    print(f"[GPT 호출 실패] {redact(str(e))}  ({type(e).__name__})")
    cause, seen = e.__cause__ or e.__context__, set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        print(f"    └ 원인: {type(cause).__name__}: {redact(str(cause))}")
        cause = cause.__cause__ or cause.__context__


# API 장애·한도 초과처럼 '일시적으로' 답을 못 만든 경우의 응답.
# "자료가 없어서 못 답함"과 구분해야 한다.
SERVICE_BUSY = ("죄송해요, 지금 답변을 준비하는 데 문제가 생겼어요. "
                "잠시 후 다시 여쭤봐 주시겠어요? "
                "급하시면 고객센터 1600-1976으로 문의하셔도 됩니다.")


# ─────────────────────────────────────────────────────────────
# 6. 실제 호출 (일반)
# ─────────────────────────────────────────────────────────────

def ask_gpt(question, context, history=None):
    """참고 자료 + 직전 대화 + 질문을 보내 답변을 받는다."""
    client, messages, err = _prepare(question, context, history)
    if err:
        return err

    try:
        for attempt in range(3):
            try:
                res = client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    temperature=0.2,      # 낮을수록 자료에 충실
                    max_tokens=400,
                )
                return res.choices[0].message.content.strip()
            except Exception as e:
                # 분당 한도면 잠깐 쉬고 재시도, 하루 한도면 포기
                wait = _retry_after(e)
                if wait is None or attempt == 2:
                    raise
                print(f"[한도 초과] {wait:.0f}초 후 재시도 ({attempt + 1}/2)")
                time.sleep(wait)
    except Exception as e:
        # 서버가 500으로 죽지 않게 여기서 흡수한다
        _log_error(e)
        return SERVICE_BUSY


# ─────────────────────────────────────────────────────────────
# 7. 실제 호출 (스트리밍) — 화면에 글자가 한 자씩 나오게
# ─────────────────────────────────────────────────────────────

def ask_gpt_stream(question, context, history=None):
    """답변을 조각으로 하나씩 내놓는다(제너레이터)."""
    client, messages, err = _prepare(question, context, history)
    if err:
        yield err
        return

    try:
        stream = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.2,
            max_tokens=400,
            stream=True,          # ← 이것만 추가하면 조각으로 온다
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            piece = chunk.choices[0].delta.content
            if piece:
                yield piece
    except Exception as e:
        _log_error(e)
        yield SERVICE_BUSY


# ─────────────────────────────────────────────────────────────
# 참고: 한도 초과 시 실제로 뜨는 메시지
#
# [터미널]
#   [GPT 호출 실패] Error code: 429 - {'error': {'message': 'Rate limit reached for
#   gpt-4o-mini in organization org-0mvev2qGfXIZ2DiNJRrguYmH on requests per day
#   (RPD): Limit 50, Used 50, Requested 1. Please try again in 28m48s.',
#   'code': 'rate_limit_exceeded'}}  (RateLimitError)
#       └ 원인: HTTPStatusError: Client error '429 Too Many Requests'
#
# [고객 화면]
#   죄송해요, 지금 답변을 준비하는 데 문제가 생겼어요. 잠시 후 다시 여쭤봐 주시겠어요?
#
#
# 확인 부탁드릴 것
#   1. Settings → Billing  — 크레딧 잔액이 실제로 들어와 있는지
#   2. Settings → Limits   — Usage tier가 Free인지 Tier 1인지
#   3. Free라면 결제 수단 등록 (5달러는 가승인, 7일 내 자동 해제)
#   4. Spend limit을 월 $10 정도로 걸어두면 그 이상 과금되지 않음
# ─────────────────────────────────────────────────────────────
