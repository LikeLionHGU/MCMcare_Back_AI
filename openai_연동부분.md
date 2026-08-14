# OpenAI 연동 부분만 발췌

`as_chatbot.py`에서 OpenAI와 관련된 코드만 뽑았습니다.
결제·조직 설정 확인하실 때 참고하세요.

---

## 1. 설정 요약

| 항목 | 값 |
|---|---|
| 모델 | `gpt-4o-mini` |
| 조직 | `org-0mvev2qGfXIZ2DiNJRrguYmH` (사다리타기) |
| 키 위치 | `.env` 파일의 `OPENAI_API_KEY` (Git 제외됨) |
| temperature | `0.2` (낮을수록 자료에 충실) |
| max_tokens | `400` (답변 길이 상한) |
| timeout | 30초 |
| 요청당 입력 토큰 | 평균 약 400~1,200 |

**현재 문제**: 무료 티어 한도 — **하루 50회 / 분당 10회**
결제 수단 등록 시 하루 10,000회 / 분당 500회로 상향됩니다.

지금까지 사용량: **62회 호출에 $0.01 (약 15원)**

---

## 2. 키 읽기 (.env)

```python
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

from dotenv import load_dotenv
load_dotenv(_ENV_PATH, override=False)   # export 한 값이 있으면 그쪽 우선
```

`.env` 내용:

```
OPENAI_API_KEY=sk-...
MCM_MOCK=          # 1이면 GPT 호출 안 함 (테스트용)
```

---

## 3. 클라이언트 생성 + 키 검증

```python
MODEL = "gpt-4o-mini"

api_key = (os.getenv("OPENAI_API_KEY") or "").strip()

if not api_key:
    return "[오류] OPENAI_API_KEY가 없습니다."
if not api_key.startswith("sk-"):
    return "[오류] 'sk-'로 시작해야 합니다."
if any(c.isspace() for c in api_key) or not api_key.isascii():
    # 키를 복사할 때 줄바꿈·공백이 섞이면 HTTP 헤더가 깨져
    # LocalProtocolError가 나는데 겉으로는 "Connection error"로 보인다
    return "[오류] API 키에 공백·줄바꿈 또는 비ASCII 문자가 섞여 있습니다."

client = OpenAI(api_key=api_key, timeout=30.0)
```

---

## 4. 실제 호출

```python
res = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "system", "content": SYSTEM_PROMPT}]
             + prior_conversation          # 직전 3턴
             + [{"role": "user", "content": user_msg}],
    temperature=0.2,
    max_tokens=400,
)
answer = res.choices[0].message.content.strip()
```

`user_msg` 형태:

```
[참고 자료]
(우리 지식베이스에서 검색된 내용 — 상품 정보, AS 안내, 접수 건)

[고객 질문]
(개인정보를 지운 질문)
```

스트리밍 버전은 `stream=True`만 추가하고 조각을 이어 붙입니다.

---

## 5. 429(한도 초과) 처리

```python
def _retry_after(e):
    """429일 때 몇 초 뒤 재시도하면 되는지 알아낸다."""
    msg = str(e)
    if not any(k in msg for k in ("429", "rate_limit", "Rate limit")):
        return None
    m = re.search(r"try again in\s*(?:(\d+)m)?\s*([\d.]+)\s*(ms|s)\b", msg)
    ...
    if sec > 20:
        return None          # 하루 한도(수십 분)면 기다려도 소용없으니 포기
    return sec + 1           # 분당 한도(수 초)면 자동 재시도
```

- **분당 한도**: 자동으로 기다렸다 재시도 (최대 2회)
- **하루 한도**: 재시도 포기 → 고객에게 "일시적 오류" 안내

서버는 죽지 않고 계속 동작합니다.

---

## 6. 한도 초과 시 나오는 메시지

**터미널**

```
[GPT 호출 실패] Error code: 429 - {'error': {'message': 'Rate limit reached for
gpt-4o-mini in organization org-0mvev2qGfXIZ2DiNJRrguYmH on requests per day (RPD):
Limit 50, Used 50, Requested 1. Please try again in 28m48s.',
'code': 'rate_limit_exceeded'}}  (RateLimitError)
    └ 원인: HTTPStatusError: Client error '429 Too Many Requests'
```

**고객 화면**

```
죄송해요, 지금 답변을 준비하는 데 문제가 생겼어요.
잠시 후 다시 여쭤봐 주시겠어요? 급하시면 고객센터 1600-1976으로 문의하셔도 됩니다.
```

---

## 7. 안전장치 (참고)

OpenAI로 **보내기 전에** 개인정보를 지웁니다.

```python
PII_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[이메일]"),
    (re.compile(r"(?<!\d)(?:\d{4}[-\s]?){3}\d{4}(?!\d)"), "[카드번호]"),
    (re.compile(r"(?<!\d)\d{6}[-\s]?[1-4]\d{6}(?!\d)"), "[주민번호]"),
    (re.compile(r"(?<!\d)01[016-9][-\s.]?\d{3,4}[-\s.]?\d{4}(?!\d)"), "[전화번호]"),
    ...
]
```

에러 로그에도 API 키가 안 남게 가립니다.

```python
def redact(text):
    return re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-***REDACTED***", text)
```

---

## 확인 부탁드릴 것

1. **Settings → Billing** — 크레딧 잔액이 실제로 들어와 있는지
2. **Settings → Limits** — Usage tier가 Free인지 Tier 1인지
3. Free라면 결제 수단 등록 (5달러는 가승인, 7일 내 자동 해제)
4. 가능하면 **Spend limit을 월 $10** 정도로 걸어두면 그 이상 과금 안 됩니다
