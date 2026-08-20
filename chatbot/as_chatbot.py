#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Custodia - MCM 브랜드 AS AI 상담 챗봇

[구조]
  질문 → ① 우리 자료에서 관련 부분 검색 → ② GPT에 [자료+질문] 전송 → ③ 답변
  GPT는 문장을 다듬는 역할만 하고, 내용은 전부 우리 자료에서 나온다.
  이 방식을 RAG(검색 증강 생성)라고 부른다.

[안전장치]
  1. 자료에 없는 내용은 답하지 않고 "확인 필요 / 상담원 연결"로 유도
  2. 금액 문의는 AI 예상 견적 기능으로 유도 (챗봇이 금액을 지어내지 않게)
  3. 고객 개인정보는 애초에 GPT로 보내지 않는다 (프롬프트로 막는 게 아니라 구조적으로 차단)

[준비]
  pip install openai fastapi "uvicorn[standard]"
  export OPENAI_API_KEY="sk-..."        # 맥/리눅스
  set OPENAI_API_KEY=sk-...             # 윈도우

[실행]
  python as_chatbot.py                        # 터미널에서 바로 대화 (서버 없이 테스트)
  python as_chatbot.py "얼마나 걸려요?"        # 질문 한 개만
  python as_chatbot.py --test                 # 9개 케이스 자가 점검 (검색·마스킹 확인)
  uvicorn as_chatbot:app --reload             # 서버로 띄우기 → http://127.0.0.1:8000/docs

[돈 안 쓰고 테스트]
  MCM_MOCK=1 python as_chatbot.py --test      # GPT를 아예 호출하지 않는다
"""

import csv
import datetime
import hashlib
import json
import os
import re
import sys
import time

# ─────────────────────────────────────────────────────────────
# 0. 설정 읽기 (.env 파일)
#
#    API 키를 코드에 직접 적으면 Git에 그대로 올라간다.
#    GitHub은 이걸 감지해서 OpenAI에 알리고, 키가 자동으로 폐기된다.
#    그래서 키는 .env 파일에 두고, 그 파일은 Git에서 제외한다.
#
#    터미널에서 export 한 값이 있으면 그쪽이 우선이다. (override=False)
# ─────────────────────────────────────────────────────────────

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

try:
    from dotenv import load_dotenv
    if load_dotenv(_ENV_PATH, override=False):
        print(f"[설정] .env 파일을 읽었습니다. ({_ENV_PATH})")
except ImportError:
    # python-dotenv가 없어도 export 방식으로는 계속 동작한다
    if os.path.exists(_ENV_PATH):
        print("[안내] .env 파일이 있지만 python-dotenv가 없어 읽지 못했습니다. "
              "pip install python-dotenv")


# ─────────────────────────────────────────────────────────────
# 1. 지식베이스 — AS 안내 (기획 화면에 적힌 내용 그대로)
#    챗봇은 여기 있는 내용으로만 답한다. 여기 없으면 "모른다"고 해야 한다.
# ─────────────────────────────────────────────────────────────

AS_KNOWLEDGE = [
    {
        "topic": "접수 준비물",
        "keywords": ["준비", "영수증", "보증서", "정품", "구매", "주문번호", "필요", "서류",
                     "챙겨", "가져", "지참", "뭐가 있어야", "무엇이 필요"],
        "content": (
            "AS 접수 전 준비 사항:\n"
            "1) 구매 영수증 또는 정품 보증서 - 구매 이력 확인에 사용됩니다. "
            "온라인 구매 고객은 주문번호로 대체 가능합니다.\n"
            "2) 제품 사진 (손상 부위 포함) - 손상 유형 분류와 예상 견적 안내에 활용됩니다. "
            "선명한 사진을 준비해 주세요.\n"
            "3) 수거 주소 및 픽업 가능 일정 - 접수 후 픽업 예약 단계에서 날짜와 시간대를 선택합니다.\n"
            "4) 부속품 분리 및 개인 소지품 제거 - 제품 본체만 인계해 주세요. "
            "분실 방지를 위해 내부 소지품을 미리 꺼내 주시기 바랍니다."
        ),
    },
    {
        "topic": "소요 기간",
        "keywords": ["기간", "얼마나", "며칠", "오래", "걸리", "시간", "빨리", "언제", "소요",
                     "몇 주", "몇주", "며칠이나", "얼마쯤", "언제쯤", "당일", "급해"],
        "content": (
            "예상 소요 안내:\n"
            "- 접수 및 픽업 예약: 약 10분\n"
            "- AI 예상 견적 안내: 사진 제출 후 즉시\n"
            "- 수선 소요 기간: 손상 유형에 따라 상이하며 최소 2주\n"
            "- 최종 견적 확정: 실물 진단 완료 후 안내"
        ),
    },
    {
        "topic": "견적 및 비용",
        "keywords": ["비용", "얼마", "가격", "견적", "돈", "요금", "무료", "유료", "원",
                     "값", "얼마예", "얼마정도", "얼마나 나", "비싸", "저렴", "부담",
                     "청구", "결제", "지불"],
        "content": (
            "예상 견적은 손상 사진을 제출하면 AI가 수선 비용 범위를 안내합니다. "
            "예상 견적은 참고용이며, 실물 진단 후 최종 비용이 확정됩니다.\n"
            "구체적인 금액은 'AI 예상 견적 받기'에서 사진을 올려야 확인할 수 있습니다. "
            "챗봇은 금액을 직접 안내하지 않습니다."
        ),
    },
    {
        "topic": "AS 서비스 품질",
        "keywords": ["정품", "부자재", "기술", "품질", "공인", "믿을", "안전"],
        "content": (
            "Custodia의 MCM AS는 정품 부자재와 공인 수선 기술을 사용합니다."
        ),
    },
    {
        "topic": "수거 및 배송",
        "keywords": ["수거", "픽업", "배송", "기사", "보험", "파손", "분실", "택배"],
        "content": (
            "신원 확인된 기사가 제품을 직접 수거하며, 운송 구간 전체에 보험이 적용됩니다."
        ),
    },
    {
        "topic": "진행 상황 확인",
        "keywords": ["진행", "상황", "어디", "확인", "조회", "상태", "패스포트", "리페어"],
        "content": (
            "수선 진행 상황은 리페어 패스포트에서 실시간으로 확인하실 수 있습니다."
        ),
    },
    {
        # MCM 공식몰 상품 페이지의 '관리' 섹션 내용 (2026-08-10 확인)
        "topic": "제품 관리법",
        "keywords": ["관리", "보관", "세탁", "젖", "물", "얼룩", "청소", "닦", "손질",
                     "긁", "스크래치", "가죽", "캔버스", "먼지", "더스트백", "습기", "곰팡이"],
        "content": (
            "MCM 제품 관리 안내 (공식 안내 기준):\n"
            "- 보관: 함께 제공되는 더스트백에 넣어 직사광선이나 밝은 빛을 피해 "
            "서늘하고 건조한 곳에 보관해 주세요.\n"
            "- 가죽 제품은 젖거나 얼룩이 생기지 않도록 주의하세요.\n"
            "- 표면이 젖었을 경우: 보풀이 없고 밝은색의 흡수성 천으로 물기를 닦아주세요.\n"
            "- 금지 사항: 비누나 솔벤트로 표면을 닦지 마세요.\n"
            "- 거친 표면에 긁히지 않도록 조심하세요.\n"
            "- 적절히 관리된 가죽 제품은 시간이 지나며 자연스러운 색 변화가 생깁니다."
        ),
    },
    {
        "topic": "접수 방법",
        # 고객이 실제로 쓰는 표현을 최대한 담는다.
        # "내 가방을 수선 맡기고 싶은데"가 검색 0건이 나서 상담원으로 빠지던 사고를 겪었다.
        "keywords": ["접수", "신청", "어떻게", "시작", "방법", "절차",
                     "수선", "수리", "고치", "고쳐", "맡기", "맡길", "보내", "의뢰",
                     "하고싶", "하고 싶", "하려면", "받으려면", "이용", "진행하고",
                     "as", "AS", "케어", "서비스", "예약", "찢어", "터졌", "터짐",
                     "떨어졌", "떨어짐", "지퍼", "손잡이", "핸들", "스트랩", "어깨끈",
                     "긁힘", "찍힘", "오염", "변색", "낡", "해졌", "벗겨", "손상", "고장",
                     # 2026-08-13 보강: 실제 고객 표현인데 빠져 있던 것들
                     "망가", "부서", "빠졌", "빠짐", "풀렸", "풀림", "뜯어", "뜯김",
                     "헐거", "닳", "구멍", "깨졌", "휘었", "버클", "잠금", "똑딱"],
        "source": "기획 화면 문구",
        "content": (
            "'AS 접수 시작하기' 버튼을 눌러 제품 정보 입력부터 시작합니다. "
            "제품명, 구매일, 손상 부위 등을 입력한 뒤 픽업 예약 단계로 넘어갑니다.\n"
            "접수 전에 예상 견적만 먼저 확인하고 싶다면 'AI 예상 견적 받기'를 이용할 수 있습니다."
        ),
    },

    # ── 아래부터는 MCM 공식 사이트에서 확인한 내용 (2026-08-12) ──
    # 출처를 남기는 이유: 정책은 바뀐다. 나중에 누가 이 문장의 근거를 물으면 답할 수 있어야 한다.
    {
        "topic": "교환 및 반품",
        "keywords": ["교환", "반품", "환불", "취소", "물리", "바꾸", "돌려", "무르",
                     "변심", "반송", "반송비", "배송비", "며칠 이내", "기간 내",
                     "누가 내", "부담", "택", "tag", "불량"],
        "source": "MCM 공식몰 배송&환불 FAQ (2026-08-12 확인)",
        "content": (
            "교환 및 반품 안내 (MCM 공식 온라인 스토어 기준):\n"
            "- 상품 수령 후 사용하지 않은 제품에 한해 15일 이내 교환·반품이 가능합니다.\n"
            "- 상품, 택(TAG), 포장 상태를 확인한 뒤 반품 처리됩니다.\n"
            "- 교환·반품은 MCM 공식 온라인 스토어에서 구매한 건만 가능합니다.\n"
            "- 제품 불량인 경우 동일 제품에 한해 교환이 가능합니다.\n"
            "- 접수는 1:1 고객 문의 게시판으로 요청해 주세요.\n"
            "- 단순 변심에 의한 반송비는 고객 부담입니다. "
            "CJ대한통운 이용 시 왕복 5,000원, 타 택배 이용 시 출고 택배비 2,500원이 발생합니다.\n"
            "- 언더웨어·라운지웨어는 로브를 제외하고 교환·반품이 불가합니다.\n"
            "※ 수선(AS)과 교환·반품은 별개 절차입니다. 수선은 'AS 접수 시작하기'를 이용해 주세요."
        ),
    },
    {
        "topic": "배송 안내",
        "keywords": ["배송", "택배", "발송", "도착", "받아", "언제 와", "언제 오",
                     "운송장", "송장", "무료배송", "배송비", "포장", "선물포장",
                     "반환", "돌려받", "받는", "수령"],
        "source": "MCM 공식몰 배송&환불 FAQ (2026-08-12 확인)",
        "content": (
            "배송 안내 (MCM 공식 온라인 스토어 기준):\n"
            "- 결제 완료 후 CJ대한통운을 통해 1~2일 이내 배송이 시작됩니다.\n"
            "- MCM 공식몰의 모든 주문은 무료 배송입니다.\n"
            "- 지속 가능한 가치를 위해 별도 선물 포장 대신 시그니처 쇼핑백을 제공합니다.\n"
            "수선 제품 반환:\n"
            "- 수선이 완료되면 접수 시 선택한 반환 방법으로 발송됩니다.\n"
            "- 등록된 주소로 택배 발송되며, 발송 시 문자로 운송장 번호를 안내드립니다."
        ),
    },
    {
        "topic": "고객센터 안내",
        # '번호', '메일'처럼 넓은 단어는 넣지 않는다.
        # 고객이 자기 전화번호를 적었을 때 이 자료가 딸려오는 사고가 있었다.
        "keywords": ["상담원", "고객센터", "연락처", "전화 문의", "전화번호 알려",
                     "문의처", "상담 가능", "영업시간", "운영시간", "몇 시까지",
                     "몇 시부터", "이메일 주소", "메일 주소", "채팅 상담",
                     "주말에", "공휴일"],
        "source": "Custodia 상담센터 (피그마 푸터 · 2026-08-19 확인)",
        "content": (
            "상담센터 안내:\n"
            "- AS 전담 직통번호: 1588-0001 "
            "(평일 09:00~18:00, 토요일 10:00~15:00)\n"
            "- 고객 상담 대표번호: 1588-0000 (평일 09:00~18:00)\n"
            "- AS 관련 문의는 전담 직통번호로 안내한다.\n"
            "- 이메일: contact.kr@mcmworldwide.com"
        ),
    },
    {
        "topic": "제품 표기 주의사항",
        "keywords": ["오차", "정확", "실제", "달라", "다르게", "모니터", "화면",
                     "색이", "색상 차이", "사이즈 오차", "치수 오차"],
        "source": "MCM 공식몰 배송&환불 FAQ (2026-08-12 확인)",
        "content": (
            "제품 정보 표기 관련 안내:\n"
            "- 사이즈는 측정 기준에 따라 약간의 오차가 있을 수 있습니다.\n"
            "- 모니터 사양에 따라 실제 색상과 다르게 보일 수 있습니다."
        ),
    },
    {
        "topic": "구성품 및 보증서 위치",
        "keywords": ["보증서", "워런티", "워런티카드", "보증카드", "더스트백", "구성품",
                     "같이 왔", "동봉", "박스", "케이스", "어디 있", "잃어버렸",
                     "없는데", "분실했"],
        "source": "MCM 공식몰 상품 FAQ (2026-08-12 확인)",
        "content": (
            "제품 구성품 안내:\n"
            "- 핸드백: 제품 내부에 더스트백과 **워런티카드(정품 보증서)**가 동봉되어 출고됩니다.\n"
            "- 지갑·액세서리: 브랜드 전용 하드케이스에 더스트백과 함께 동봉되어 출고됩니다.\n"
            "AS 접수 시 정품 보증서가 필요한데, 구매 시 제품과 함께 받으신 "
            "워런티카드가 그것입니다. 가방 내부나 포장 박스를 확인해 주세요.\n"
            "보증서를 찾지 못하셨다면 구매 영수증으로도 확인 가능하며, "
            "온라인 구매 고객은 주문번호로 대체할 수 있습니다."
        ),
    },
    {
        "topic": "주문 내역 확인",
        "keywords": ["주문번호", "주문 번호", "구매 이력", "주문내역", "언제 샀",
                     "구매일", "영수증 없", "산 날짜", "내 계정", "마이페이지"],
        "source": "MCM 공식몰 상품 FAQ (2026-08-12 확인)",
        "content": (
            "과거 구매 내역과 주문번호 확인 방법:\n"
            "- MCM 공식몰 상단 메뉴의 [내 계정] → [주문내역]에서 확인하실 수 있습니다.\n"
            "- AS 접수 시 구매일과 주문번호가 필요한데, 이곳에서 함께 확인 가능합니다.\n"
            "- 매장에서 구매하신 경우에는 구매 영수증이나 워런티카드를 확인해 주세요."
        ),
    },
    {
        "topic": "가격 변동 안내",
        "keywords": ["가격 바뀌", "가격이 달라", "할인", "세일", "더 싸", "차액",
                     "가격 변동", "예전에 샀", "가격 왜"],
        "source": "MCM 공식몰 상품 FAQ (2026-08-12 확인)",
        "content": (
            "상품 가격 관련 안내:\n"
            "- 온라인 판매 상품은 시장 가격 변동에 따라 가격이 수시로 조정될 수 있습니다.\n"
            "- 구매 이후 가격이 인하되더라도 차액 환불은 어렵습니다.\n"
            "- 표시 가격에 오류가 의심되면 상품명 또는 상품코드와 함께 "
            "1:1 고객 문의로 알려주시면 확인 후 수정합니다.\n"
            "※ 챗봇이 안내하는 판매가는 수집 시점 기준이므로 실제와 다를 수 있습니다."
        ),
    },
    {
        "topic": "정품 인증 및 디지털 제품 패스포트",
        "keywords": ["정품 맞", "정품인지", "정품 확인", "가품", "짝퉁", "진품", "진짜",
                     "인증", "패스포트", "passport", "nfc", "칩", "블록체인", "진위"],
        "source": "MCM 공식몰 디지털 제품 패스포트 안내 (2026-08-10 확인)",
        "content": (
            "MCM 디지털 제품 패스포트(DPP) 안내:\n"
            "- 일부 제품에는 NFC 칩이 내장되어 있어 스마트폰으로 태그하면 "
            "제품의 정품 여부와 소유 이력, 원재료 정보를 확인할 수 있습니다.\n"
            "- 제품별 적용 여부는 상이하므로 보유하신 제품 기준 확인이 필요합니다.\n"
            "- AS 접수 시에는 구매 영수증 또는 정품 보증서로 구매 이력을 확인합니다."
        ),
    },
]

# ─────────────────────────────────────────────────────────────
# 2. 지식베이스 — 상품 정보 (크롤러가 수집한 CSV)
# ─────────────────────────────────────────────────────────────

# 이 파일이 있는 폴더 기준으로 CSV를 찾는다.
# (다른 폴더에서 uvicorn을 실행해도 상품 정보가 사라지지 않게)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCT_CSV = os.path.join(BASE_DIR, "mcm_products.csv")


def load_products(path=PRODUCT_CSV):
    """크롤러가 만든 CSV를 읽어 상품 지식으로 바꾼다. 파일이 없으면 빈 목록."""
    if not os.path.exists(path):
        print(f"[안내] {path} 가 없어 상품 정보 없이 동작합니다. (AS 안내만 답변)")
        return []

    items, seen = [], set()
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            # 같은 상품이 여러 카테고리 경로로 접근 가능해서 CSV에 중복으로 들어온다.
            #   /ko_KR/스타크-.../MMKEAVE14CO001.html
            #   /ko_KR/남성/가방/백팩/스타크-.../MMKEAVE14CO001.html   ← 같은 상품
            # 그대로 두면 "8가지 제품" 목록에 같은 상품이 3번 나온다. (실제로 났던 사고)
            code = r["product_code"].strip()
            if not code or code in seen:
                continue
            seen.add(code)

            parts = [f"상품명: {r['product_name']}", f"상품코드: {r['product_code']}"]
            label = {"color": "색상", "material_body": "본체 소재", "material_trim": "트림 소재",
                     "material_lining": "안감", "hardware": "하드웨어",
                     "dimensions": "크기(cm)", "strap_length": "스트랩 길이",
                     "handle_drop": "핸들 길이", "origin_country": "제조국"}
            for key, ko in label.items():
                if r.get(key, "").strip():
                    parts.append(f"{ko}: {r[key]}")
            # 가격은 원본에 쉼표나 소수점이 섞여 있을 수 있어 숫자만 남긴 뒤 변환한다.
            # (변환에 실패하면 가격 줄을 그냥 넣지 않는다 — 챗봇이 죽는 것보다 낫다)
            raw_price = re.sub(r"[^0-9]", "", r.get("price_krw", ""))
            if raw_price:
                parts.append(f"판매가: {int(raw_price):,}원 (제품 정가 / 수선 비용 아님)")

            # 검색용 키워드: 상품명·코드·색상·소재를 잘게 쪼개 담는다
            kw = re.split(r"[\s,|]+", " ".join(
                [r["product_name"], r["product_code"], r.get("color", ""),
                 r.get("material_body", ""), r.get("material_trim", "")]))
            items.append({
                "topic": f"상품 {r['product_code']}",
                "keywords": [k for k in kw if len(k) >= 2],
                "content": "\n".join(parts),
            })
    return items


# ─────────────────────────────────────────────────────────────
# 2-B. AS 접수 데이터 (데모용 더미)
#      실서비스에서는 DB 조회로 바뀐다. 읽는 곳만 바뀌고 나머지 코드는 그대로다.
# ─────────────────────────────────────────────────────────────

REPAIR_JSON = os.path.join(BASE_DIR, "as_dummy.json")


def _d(days_ago):
    """'며칠 전'을 실제 날짜 문자열로 바꾼다. None이면 아직 안 일어난 일."""
    if days_ago is None:
        return None
    return (datetime.date.today() - datetime.timedelta(days=days_ago)).strftime("%Y년 %m월 %d일")


def _d_future(days):
    if days is None:
        return None
    return (datetime.date.today() + datetime.timedelta(days=days)).strftime("%Y년 %m월 %d일")


def load_repairs(path=REPAIR_JSON):
    """
    더미 접수 데이터를 읽어 '오늘 기준' 날짜로 바꿔 돌려준다.

    날짜를 파일에 고정값으로 박지 않는 이유:
    발표일이 언제든 "3주 전에 접수한 건"으로 자연스럽게 보이게 하기 위함.
    """
    if not os.path.exists(path):
        print(f"[안내] {path} 가 없어 접수 데이터 없이 동작합니다.")
        return {}, []

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    repairs = []
    for r in data.get("repairs", []):
        item = dict(r)
        item["received_at"] = _d(r.get("received_days_ago"))
        item["expected_at"] = _d_future(r.get("expected_days_from_now"))
        item["updated_at"] = _d(r.get("updated_days_ago"))
        item["timeline"] = [
            {**t, "date": _d(t.get("days_ago")), "done": t.get("days_ago") is not None}
            for t in r.get("timeline", [])
        ]
        repairs.append(item)
    return data.get("customer", {}), repairs


CUSTOMER, REPAIRS = load_repairs()

# 광은님 Spring 서버 연동 (선택).
# SPRING_API_URL 환경변수가 없으면 이 모듈은 아무 일도 하지 않는다.
# 즉 지금까지처럼 as_dummy.json으로 동작한다. 기존 동작을 깨지 않기 위함.
try:
    import spring_client
except ImportError:
    spring_client = None


def _norm_name(text):
    """상품명 비교용 정규화. 공백·대소문자 차이로 못 찾는 일을 막는다."""
    return re.sub(r"\s+", "", (text or "")).lower()


# 상품명 → 상품코드 역방향 표. 서버가 코드를 안 줄 때 쓴다.
# 같은 이름이 여러 코드에 걸리면(실제로 있다) 넣지 않는다.
# 잘못된 코드 하나가 엉뚱한 제품의 치수를 답하게 만들기 때문.
_NAME_TO_CODE = None


def resolve_product_code(product_name):
    """모델명으로 상품코드를 찾는다. 못 찾거나 여러 개면 None."""
    global _NAME_TO_CODE
    if _NAME_TO_CODE is None:
        table = {}
        for item in KNOWLEDGE:
            if item in AS_KNOWLEDGE or not item["topic"].startswith("상품 "):
                continue
            code = item["topic"].replace("상품 ", "").strip()
            first = item["content"].splitlines()[0]
            name = _norm_name(first.replace("상품명: ", ""))
            if not name:
                continue
            if name in table and table[name] != code:
                table[name] = None            # 중복 → 쓰지 않는다
            else:
                table.setdefault(name, code)
        _NAME_TO_CODE = table
    return _NAME_TO_CODE.get(_norm_name(product_name))


def color_of_code(product_code):
    """
    상품코드로 색상을 찾는다. 없으면 None.

    왜: 피그마 '제품 정보 입력 화면'에 색상 입력란이 없고 DetailResDto에도 색상이 없다
        (광은님 8/18 코멘트). 그래도 상품코드만 알면 우리가 수집한 750건에서
        색상을 꺼내 쓸 수 있다. 고객에게 다시 묻지 않아도 된다.
    """
    if not product_code:
        return None
    topic = f"상품 {str(product_code).strip().upper()}"
    for item in KNOWLEDGE:
        if item["topic"].upper() == topic:
            m = re.search(r"^색상: (.+)$", item["content"], re.M)
            return m.group(1).strip() if m else None
    return None


def find_repair(as_id):
    """
    접수번호로 한 건을 찾는다. 대소문자·공백은 무시한다.

    조회 순서:
      1) Spring 서버 (SPRING_API_URL이 설정돼 있을 때만)
      2) as_dummy.json  — Spring을 안 쓸 때만

    [더미 폴백을 없앤 이유]
    전에는 Spring 조회가 실패하면 as_dummy.json으로 넘어갔다.
    서버가 잠깐 죽으면 챗봇이 김민서의 가짜 접수 건을 진짜처럼 설명한다.
    에러가 안 나니 아무도 눈치채지 못한다 — 조용히 틀린 답을 하는 게 제일 나쁘다.

    그래서 Spring을 쓰기로 했으면 더미로 내려가지 않는다.
    못 찾으면 못 찾았다고 말한다. 왜 못 찾았는지는 lookup_failure()가 알려준다.
    """
    if not as_id:
        return None
    key = str(as_id).strip().upper()

    if spring_client is not None and spring_client.is_enabled():
        found = spring_client.fetch_repair(key)
        if not found:
            return None            # 더미로 내려가지 않는다
        if found:
            # 서버의 DetailResDto에는 상품코드가 없고 모델명만 있다.
            # 챗봇은 상품코드로 소재·치수를 찾으므로, 코드가 비어 있으면
            # 모델명으로 우리 상품 자료에서 역으로 찾아 채운다.
            # (없으면 그냥 비워둔다 — 틀린 코드를 넣는 것보다 낫다)
            if not found.get("product_code") and found.get("product_name"):
                code = resolve_product_code(found["product_name"])
                if code:
                    found["product_code"] = code
            if not found.get("color"):
                found["color"] = color_of_code(found.get("product_code")) or ""
            return found

    for r in REPAIRS:
        if r["as_id"].upper() == key:
            return r
    return None


def lookup_failure():
    """
    직전 접수 건 조회가 왜 실패했는지. 고객에게 할 말이 다르다.

      "not_found"  그런 접수번호가 없거나 본인 건이 아니다  → 번호 확인 안내
      "error"      서버에 못 닿았다                        → 잠시 후 재시도 안내
      ""           Spring을 안 쓰는 중이거나 성공했다
    """
    if spring_client is None or not spring_client.is_enabled():
        return ""
    return spring_client.last_failure()


def _join_product(repair):
    """
    제품 표기. 있는 정보만 붙인다.

    왜: 접수 화면에 색상 입력란이 없고(피그마 '제품 정보 입력 화면'),
        DetailResDto에도 상품코드가 없다. 그대로 포맷하면
        "제품: Stark 백팩 () []" 처럼 빈 괄호가 남아 GPT가 그걸 따라 쓴다.
    """
    parts = [repair.get("product_name") or "제품"]
    if repair.get("color"):
        parts.append(f"({repair['color']})")
    if repair.get("product_code"):
        parts.append(f"[{repair['product_code']}]")
    return " ".join(parts)


def _join_damage(repair):
    """손상 표기. 유형·부위 중 있는 것만 붙인다."""
    kind = repair.get("damage_type") or ""
    part = repair.get("damage_part") or ""
    if kind and part:
        return f"{kind} ({part})"
    return kind or part or "확인 필요"


def _ai_damage_note(repair):
    """
    AI 판정 손상. 고객이 고른 것과 다르면 그 사실을 같이 알려준다.

    왜: 고객은 "긁힘"이라 골랐는데 AI는 "찢김/파열"로 볼 수 있다.
        다른 게 오류가 아니라 정보다 — 실물 진단 때 함께 확인하겠다고 안내할 수 있다.
        같으면 굳이 두 번 말하지 않는다.
    """
    ai = (repair.get("damage_category") or "").strip()
    if not ai:
        return None
    mine = (repair.get("damage_type") or "").strip()
    if ai == mine:
        return None
    return (f"{ai} (고객이 접수 때 고른 것은 '{mine}'이다. "
            "다르게 보인다는 점만 알리고, 최종 판단은 실물 진단 후라고 안내한다.)"
            if mine else ai)


# ─────────────────────────────────────────────────────────────
# AS 단계별 안내 (스프링 AsStatus 9단계 기준)
#
# 왜 필요한가:
#   GPT는 timeline의 [예정] 문구를 보고 "지금 진행 중"인 것처럼 답한다.
#   실제로 AS-2026-00109(완료)에서 "발송은 아직 진행 중"이라는
#   모순된 답이 나갔다. 단계별 사실을 코드에 고정해 추측을 막는다.
# ─────────────────────────────────────────────────────────────

STAGE_ORDER = [
    "접수중", "접수완료", "픽업완료", "손상부위 진단중", "손상부위 진단완료",
    "수선중", "검수중", "발송중", "완료",
]

# 스프링 코드명 → 화면 라벨. 어느 쪽이 와도 받는다.
STAGE_CODE_TO_LABEL = {
    "ESTIMATED": "접수중",
    "PICKUP_BOOKED": "접수완료",
    "PICKED_UP": "픽업완료",
    "RECEIVED": "손상부위 진단중",
    "DIAGNOSED": "손상부위 진단완료",
    "REPAIRING": "수선중",
    "INSPECTING": "검수중",
    "SHIPPING": "발송중",
    "COMPLETED": "완료",
    "CANCELLED": "접수 취소",
}

# 단계별: (지금 벌어지는 일, 고객이 할 일, 다음 단계 안내)
STAGE_GUIDE = {
    "접수중": (
        "고객이 제품 정보와 손상 사진을 입력해 접수를 진행하는 중이다. 아직 픽업 예약 전이다.",
        "예상 견적을 확인하고 픽업 예약을 완료해야 다음 단계로 넘어간다.",
        "다음: 픽업 예약(접수완료)",
    ),
    "접수완료": (
        "픽업 예약이 잡힌 상태다. 제품은 아직 고객이 가지고 있고 기사는 방문 전이다.",
        "예약한 날짜·시간에 제품을 준비해 두면 된다. 동봉할 것은 없다.",
        "다음: 기사 방문 수거(픽업완료)",
    ),
    "픽업완료": (
        "기사가 제품을 수거해 수선 센터로 이동 중이다. 아직 센터에 도착하지 않았다.",
        "따로 할 일은 없다.",
        "다음: 센터 입고 후 실물 진단(손상부위 진단중)",
    ),
    "손상부위 진단중": (
        "제품이 수선 센터에 입고되어 전문가가 실물을 확인하는 중이다. 수선 범위는 아직 확정되지 않았다.",
        "진단 결과가 나오면 안내된다. 지금은 기다리면 된다.",
        "다음: 진단 결과 확정(손상부위 진단완료)",
    ),
    "손상부위 진단완료": (
        "실물 진단이 끝나 수선 범위가 확정된 상태다. 수선 작업은 아직 시작 전이다.",
        "확정된 수선 범위와 비용을 확인하면 된다. 비용이 접수 시 예상 견적과 다를 수 있다.",
        "다음: 수선 작업 시작(수선중)",
    ),
    "수선중": (
        "수선 센터에서 실제 수선 작업이 진행 중이다. 가장 시간이 걸리는 단계다.",
        "따로 할 일은 없다. 예상 완료일을 참고하면 된다.",
        "다음: 품질 검수(검수중)",
    ),
    "검수중": (
        "수선이 끝나고 품질 기준에 맞는지 최종 점검 중이다. 아직 발송 전이다.",
        "따로 할 일은 없다.",
        "다음: 고객 배송 발송(발송중)",
    ),
    "발송중": (
        "검수를 통과해 고객에게 배송이 시작된 상태다. 제품은 이미 센터를 떠났다.",
        "운송장 번호가 있으면 배송 조회가 가능하다. 수령 시 상태를 확인하면 된다.",
        "다음: 수령 확인(완료)",
    ),
    "완료": (
        "이 접수 건은 모든 단계가 끝났다. 수선·검수·발송·배송을 포함해 진행 중인 절차가 하나도 없다.",
        "제품을 수령했다. 수선 결과에 문제가 있으면 상담원에게 문의하면 된다.",
        "다음 단계 없음. 종료된 건이다.",
    ),
    "접수 취소": (
        "이 접수 건은 취소되어 진행되지 않는다.",
        "다시 수선을 원하면 새로 접수하면 된다.",
        "다음 단계 없음. 종료된 건이다.",
    ),
}


def _normalize_stage(stage):
    """스프링이 코드명을 주든 라벨을 주든 라벨로 통일한다."""
    if not stage:
        return None
    s = str(stage).strip()
    return STAGE_CODE_TO_LABEL.get(s.upper(), s)


def stage_guide_lines(stage):
    """
    현재 단계에 해당하는 사실만 자료로 만들어 돌려준다.
    GPT가 [예정] 항목을 현재 진행 중인 일로 착각하는 것을 막는 핵심.
    """
    label = _normalize_stage(stage)
    guide = STAGE_GUIDE.get(label)
    if not guide:
        return []

    now, todo, nxt = guide
    lines = [
        "",
        f"현재 단계 '{label}'에 대한 확정 사실 (이 내용이 다른 자료보다 우선한다):",
        f"- 지금 상황: {now}",
        f"- 고객이 할 일: {todo}",
        f"- {nxt}",
    ]

    if label in ("완료", "접수 취소"):
        lines.append(
            "- 주의: 이 건은 종료되었다. 남은 절차가 있는 것처럼 말하지 마라."
        )
    else:
        idx = STAGE_ORDER.index(label) if label in STAGE_ORDER else -1
        if idx >= 0:
            remaining = STAGE_ORDER[idx + 1:]
            if remaining:
                lines.append(
                    "- 아직 오지 않은 단계: " + " → ".join(remaining)
                    + " (이 단계들은 진행 중이 아니다)"
                )
    return lines


def repair_knowledge(repair):
    """
    접수 건 하나를 '지식 항목' 형태로 바꾼다.
    이렇게 해야 상품·AS 안내와 똑같은 방식으로 GPT에 넘길 수 있다.
    """
    timeline = repair.get("timeline") or []
    done = [t for t in timeline if t.get("done")]
    todo = [t for t in timeline if not t.get("done")]

    lines = [
        "지금 상담 중인 고객의 AS 접수 건이다. 이 내용은 이 고객에게만 해당한다.",
    ]

    # 고객 멤버십 등급을 자료에 함께 넣는다.
    # 왜: MCM이 실제로 보유한 자산(고객·멤버십 데이터)을 상담에 연결하기 위함.
    #     등급을 알아야 "김민서 고객님" 같은 응대가 가능하다.
    # 주의: 등급별 혜택 문구는 확정된 자료가 없다. 지어내지 말라고 명시한다.
    # 지금 말을 건 고객이 누구인지.
    #
    # Spring 이 붙어 있으면 그 고객의 토큰으로 조회한 이름을 쓴다.
    # 전역 CUSTOMER 는 as_dummy.json 의 고객(김민서)이라, 그대로 두면
    # 누가 로그인하든 "김민서 고객님"이라고 부른다 — 남의 이름이다.
    #
    # 멤버십 등급은 Spring 에 없다. 더미의 등급을 실제 고객에게 붙이면
    # 없는 혜택을 말하게 되므로, Spring 을 쓸 때는 등급을 아예 넣지 않는다.
    me = None
    if spring_client is not None and spring_client.is_enabled():
        me = spring_client.fetch_member()

    if me or CUSTOMER:
        name = (me or CUSTOMER).get("name")
        grade = None if me else CUSTOMER.get("membership")
        if name:
            lines.append(f"고객 이름: {name} (호칭은 '{name} 고객님')")
        if grade:
            lines.append(
                f"고객 멤버십 등급: {grade}. "
                "등급은 호칭·응대에만 참고한다. "
                "등급별 할인·우선처리 같은 혜택은 확정된 자료가 없으므로 "
                "먼저 언급하지 말고, 고객이 물으면 상담원 연결로 안내한다."
            )

    # 값이 없는 줄은 아예 넣지 않는다.
    # 왜: Jackson 이 non_null 이라 광은님 응답에서 빠지는 필드가 있다.
    #     "접수일: None" 이 자료로 들어가면 GPT 가 그대로 따라 쓴다.
    for label, value in (
        ("접수번호",  repair.get("as_id")),
        ("제품",      _join_product(repair)),
        ("손상 내용", _join_damage(repair)),
        ("고객이 적은 손상 설명", repair.get("damage_description")),
        ("AI가 사진에서 판정한 손상", _ai_damage_note(repair)),
        ("접수 방식", repair.get("intake_type")),
        ("접수일",    repair.get("received_at")),
        ("현재 단계", repair.get("stage")),
    ):
        if value:
            lines.append(f"{label}: {value}")

    loc = repair.get("location")
    if loc:
        st = repair.get("location_status")
        lines.append(f"현재 위치: {loc} ({st})" if st else f"현재 위치: {loc}")

    gkey = _guide_key(repair)
    is_completed = (gkey == "COMPLETED")

    if repair.get("expected_at"):
        upd = repair.get("updated_at")
        if is_completed:
            # 완료 건은 "예상 완료일"이 아니라 "완료일"로 표기한다
            lines.append(f"완료일: {repair['expected_at']}"
                         + (f" (갱신 {upd})" if upd else ""))
        else:
            lines.append(f"예상 완료일: {repair['expected_at']}"
                         + (f" (최종 갱신 {upd})" if upd else ""))
    if repair.get("tracking_number"):
        lines.append(f"운송장 번호: {repair['tracking_number']}")

    if done or todo:
        # 순서 기반으로 [완료]/[현재]/[예정] 판정.
        # done 플래그는 스프링 데이터 정합성 문제(COMPLETED인데 SHIPPING이 done=false)가
        # 있어서 신뢰하지 않는다. 현재 단계의 인덱스를 기준으로 판정한다.
        #
        # stage는 statusLabel(Korean) 또는 status(enum 코드)가 올 수 있다.
        # status 코드가 STAGE_CODE_TO_LABEL에 정확히 매핑되므로 더 신뢰한다.
        _stage_raw = repair.get("stage") or repair.get("status") or ""
        cur_label = _normalize_stage(_stage_raw)
        # stage(Korean 라벨)가 STAGE_ORDER에 없으면 status 코드로 재시도
        if cur_label not in STAGE_ORDER:
            cur_label = _normalize_stage(repair.get("status") or "")
        cur_idx = STAGE_ORDER.index(cur_label) if cur_label in STAGE_ORDER else -1

        # timeline entry → STAGE_ORDER 인덱스.
        # spring_client는 entry["status"]에 항상 enum 코드를 넣으므로 우선 사용.
        # 없으면 step(라벨 or 코드) 으로 시도.
        def _step_idx(t):
            for key in ("status", "step"):
                label = _normalize_stage(t.get(key) or "")
                if label in STAGE_ORDER:
                    return STAGE_ORDER.index(label)
            return -1

        lines.append("진행 이력:")
        all_steps = done + todo
        for t in all_steps:
            step = t.get("step") or ""
            tail = " · ".join(x for x in (t.get("date"), t.get("note")) if x)
            suffix = f" — {tail}" if tail else ""

            if cur_idx >= 0:
                s_idx = _step_idx(t)
                if s_idx >= 0:
                    if s_idx < cur_idx:
                        tag = "[완료]"
                    elif s_idx == cur_idx:
                        tag = "[현재]"
                    else:
                        tag = "[예정]"
                else:
                    # STAGE_ORDER에 없는 값은 기존 done 플래그 그대로
                    tag = "[완료]" if t.get("done") else "[예정]"
            else:
                # 현재 단계가 STAGE_ORDER에 없으면 기존 동작 유지
                tag = "[완료]" if t.get("done") else "[예정]"

            lines.append(f"- {tag} {step}{suffix}")

    # stage_guide_lines: status(enum 코드)가 STAGE_CODE_TO_LABEL에 정확히 매핑되므로
    # 우선 사용하고, 없으면 stage(Korean 라벨 or 코드)로 시도한다.
    _guide_stage = repair.get("status") or repair.get("stage") or ""
    lines.extend(stage_guide_lines(_guide_stage))

    # 단계별 사실 안내 — GPT가 추측으로 채우는 것을 막는다
    if gkey and gkey in STAGE_GUIDE:
        lines.append(STAGE_GUIDE[gkey])

    return {
        "topic": f"내 AS 접수 {repair['as_id']}",
        "keywords": [],          # 검색으로 찾는 게 아니라 항상 앞에 붙인다
        "source": "AS 접수 데이터 (데모용)",
        "content": "\n".join(lines),
    }


# ─────────────────────────────────────────────────────────────
# 3. 검색 — 질문과 관련 있는 자료만 골라낸다
#    (24건 규모라 벡터DB 없이 키워드 매칭으로 충분하다)
# ─────────────────────────────────────────────────────────────

def _rank(question, items):
    """
    질문 단어와 겹치는 개수로 점수를 매겨 (점수, 자료) 목록을 점수 순으로 돌려준다.
    점수를 그대로 넘기는 이유: 요약을 만들 때 '겹치는 단어가 가장 많은 것들'만
    골라야 한다. 안 그러면 "비세토스 백팩" 질문에 '백팩' 하나만 겹친
    모노그램 레더 백팩까지 후보에 섞여 가격 범위가 엉뚱해진다.
    """
    q = question.lower()
    # 고객은 상품코드를 다 치지 않는다. "12CO001이요"처럼 뒷부분만 말한다.
    # 질문에서 영숫자 덩어리(4글자 이상)를 뽑아 코드의 일부인지도 따로 본다.
    q_tokens = [t.lower() for t in re.findall(r"[0-9A-Za-z]{4,}", question)]

    scored = []
    for item in items:
        keys = [k.lower() for k in item["keywords"] if k]
        score = sum(1 for k in keys if k in q)
        # 부분 코드 일치는 0.5점. 완전 일치(1점)보다는 약하게 본다.
        score += sum(0.5 for k in keys for t in q_tokens if t != k and t in k)
        if score:
            scored.append((score, item))
    scored.sort(key=lambda x: -x[0])
    return scored


PRICE_RE = re.compile(r"판매가: ([\d,]+)원")
DIM_RE = re.compile(r"크기\(cm\): (.+)")
COLOR_RE = re.compile(r"색상: (.+)")
SUMMARY_MAX = 12       # 요약에 나열할 상품 최대 개수 (토큰 폭증 방지)


def _summarize_products(matched):
    """
    상품 후보가 여러 건일 때 '총 몇 건, 얼마~얼마'를 한 덩어리로 만들어 GPT에 같이 넘긴다.

    이걸 안 넘기면 GPT는 상위 2건만 보고 그게 전부인 것처럼 답한다.
    (실제 사고: "비세토스 백팩 얼마?" → 13건인데 2건만 말하고 끝냄)
    GPT 잘못이 아니라 자료를 2건만 준 우리 잘못이다.
    """
    prices = []
    for it in matched:
        m = PRICE_RE.search(it["content"])
        if m:
            prices.append(int(m.group(1).replace(",", "")))

    lines = [f"고객이 말한 조건에 해당하는 제품은 {len(matched)}가지다. 하나가 아니다."]
    if prices:
        lines.append(f"판매가 범위: {min(prices):,}원 ~ {max(prices):,}원 "
                     f"(제품 정가 / 수선 비용 아님)")
    # 상품이 수백 건으로 늘어나면 목록이 통째로 GPT에 실려 토큰이 폭증한다.
    # 후보가 많을 때는 앞쪽 몇 개만 보여주고 나머지는 개수로 알린다.
    shown, rest = matched[:SUMMARY_MAX], max(0, len(matched) - SUMMARY_MAX)
    lines.append("해당 상품 목록:" if rest else "해당 상품 전체 목록:")
    # 제품명이 거의 같은 상품이 많다. (MCM 표기가 'Stark'와 '스타크'로 섞여 있기도 하다)
    # 이름만 나열하면 고객이 "이게 뭐가 다른데?"라고 되묻게 된다.
    # 그래서 서로 구분되는 값(색상·크기·가격)을 항상 함께 보여준다.
    for it in shown:
        body = it["content"]
        name = body.splitlines()[0].replace("상품명: ", "")
        code = it["topic"].replace("상품 ", "")
        c, d, p = (COLOR_RE.search(body), DIM_RE.search(body), PRICE_RE.search(body))
        extra = " / ".join(x for x in [
            f"색상 {c.group(1).strip()}" if c else "",
            f"크기 {d.group(1).strip()}" if d else "",
            f"{p.group(1)}원" if p else "",
        ] if x)
        lines.append(f"- {name} [{code}]" + (f" — {extra}" if extra else ""))
    if rest:
        lines.append(f"(그 외 {rest}개 더 있음. 조건을 좁히도록 되물어야 한다)")
    lines.append(
        f"답변 지침: 개수를 먼저 말하세요. 위 목록 {len(shown)}개를 "
        "빠짐없이 그대로 보여주세요. 일부만 골라 말하면 안 됩니다. "
        "제품명이 서로 비슷하므로 **색상과 크기를 반드시 함께** 적어 "
        "고객이 무엇이 다른지 알 수 있게 하세요. "
        "크기나 가격이 같은 제품끼리는 묶어서 설명해도 좋습니다. "
        "특정 상품 하나로 단정하지 말고 마지막에 어느 제품인지 물어보세요. "
        "'검색', '자료', '일치', '건' 같은 내부 표현은 쓰지 말고 "
        f"'말씀하신 제품은 {len(matched)}가지가 있어요'처럼 자연스럽게 말하세요.")

    return {"topic": f"검색 요약 (상품 {len(matched)}건 일치)",
            "keywords": [], "content": "\n".join(lines)}


# 상품별로 값이 다른 것을 묻는 질문. 이때만 '검색 요약'을 붙인다.
# "가죽이 벗겨졌는데 고칠 수 있나요"에 "총 17건입니다"로 답하면 엉뚱하다.
# 상품 자료를 '노이즈'로 판단하는 기준.
# 겹친 단어가 이 점수 이하이고(약한 일치), 동점자가 이 개수 이상이면(누구인지 못 고름)
# 상품 상세를 붙이지 않는다. 상품 수가 늘수록 중요해진다.
WEAK_SCORE = 1
TIE_LIMIT = 3

PRODUCT_QUERY_WORDS = ("얼마", "가격", "값", "치수", "크기", "사이즈", "무게",
                       "종류", "목록", "리스트", "몇 개", "몇개", "뭐가 있", "무엇이 있",
                       "어떤 게 있", "소재", "색상", "컬러")


def _asks_product_value(question):
    """
    '상품마다 값이 다른 것'을 묻는 질문인지 판단한다.

    "얼마나"를 먼저 지우는 이유:
      "제 지갑 받으려면 얼마나 남았어요?"는 기간 질문인데
      그 안의 '얼마'가 가격 질문으로 잡혀 상품 43건 목록이 딸려왔다.
      "얼마나"는 정도·기간을 묻는 말이지 가격을 묻는 말이 아니다.
    """
    q = question.replace("얼마나", "")
    return any(w in q for w in PRODUCT_QUERY_WORDS)


def search(question, knowledge, top_as=3, top_product=2, summarize_from=2):
    """
    AS 안내와 상품 정보를 '따로' 뽑아서 합친다.
    한 덩어리로 점수만 매기면, 상품이 24건이라 상품 쪽이 자리를 다 차지해서
    "비세토스 캔버스 관리법" 같은 질문에 관리 안내가 밀려난다.

    상품이 summarize_from건 이상 걸리면 요약 항목을 하나 더 붙인다.
    → GPT가 후보 일부를 전체인 것처럼 답하는 것을 막는다.
    """
    as_items = [k for k in knowledge if k in AS_KNOWLEDGE]
    product_items = [k for k in knowledge if k not in AS_KNOWLEDGE]

    hits = [it for _, it in _rank(question, as_items)[:top_as]]

    prod_scored = _rank(question, product_items)

    # 상품별 값을 묻는 질문에만 요약을 붙인다. 손상·관리 질문에는 건수가 무의미하다.
    summary = None
    if prod_scored and _asks_product_value(question):
        # 최고 점수와 같은 것들만 '진짜 후보'로 본다.
        # 점수가 낮은 것(단어 하나만 겹친 것)을 섞으면 건수·가격 범위가 왜곡된다.
        best = prod_scored[0][0]
        strong = [it for s, it in prod_scored if s == best]
        if len(strong) >= summarize_from:
            summary = _summarize_products(strong)

    # 요약을 상품 상세보다 '먼저' 넣는다.
    # 뒤에 두면 GPT가 앞의 상세 2건만 보고 "두 가지가 있어요"라고 답한다.
    # (8건인데 2건만 말하는 사고가 실제로 났다)
    if summary:
        hits.append(summary)

    # 상품 상세를 붙일지 판단한다.
    #
    # [왜 필요한가 — 2026-08-13]
    # 상품을 125건에서 크게 늘리자 '겨우 한 단어 겹친' 상품이 딸려오기 시작했다.
    #   "가방 지퍼가 고장났어요"  → '가방' 하나만 겹친 상품 14건이 동점
    #   "스카프도 수선 되나요?"   → '스카프' 하나만 겹친 상품 3건이 동점
    # 이 중 2건을 뽑아 GPT에 주면, 손상 접수를 물었는데 엉뚱한 상품을 소개한다.
    #
    # 반면 점수가 1이어도 '동점자가 없으면' 정확한 지목이다.
    #   "MMKEAVE12CO001 크기?"  → 1점이지만 딱 1건 (상품코드 일치)
    # 그래서 점수만으로 자르지 않고 '약한 점수 + 동점 다수'일 때만 뺀다.
    if prod_scored:
        best = prod_scored[0][0]
        tie = sum(1 for s, _ in prod_scored if s == best)

        # 상품코드 조각으로 걸린 것은 '약한 일치'가 아니다.
        # "12CO001이요"는 점수가 0.5지만 고객이 코드를 직접 말한 것이다.
        # 이때 후보가 여럿이면 오히려 후보를 보여주고 되물어야 한다(안전장치 4).
        # 이 예외가 없으면 연속 대화("치수 알려줘" → "12CO001이요")가 끊긴다.
        q_tokens = [t.lower() for t in re.findall(r"[0-9A-Za-z]{4,}", question)]
        by_code = any(
            t in it["topic"].replace("상품 ", "").lower()
            for _, it in prod_scored[:tie] for t in q_tokens
        )

        weak_and_ambiguous = best <= WEAK_SCORE and tie >= TIE_LIMIT and not by_code
        if not weak_and_ambiguous:
            hits += [it for _, it in prod_scored[:top_product]]
    return hits


# ─────────────────────────────────────────────────────────────
# 4. GPT 호출
# ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """당신은 Custodia의 MCM AS 상담 직원입니다.
매장에서 손님을 응대하는 직원처럼, 친절하고 담백하게 말합니다.

[내용 규칙 — 어길 수 없음]
1. 아래 '참고 자료'에 있는 내용만으로 답하세요. 없는 내용은 추측하거나 만들어내지 마세요.
2. 답할 수 없을 때는 모른다고 하지 말고, 무엇을 하면 되는지 알려주세요.
   예: "그 부분은 상담원이 직접 확인해 드리는 게 정확해요. 연결해 드릴까요?"
3. 수선 비용을 숫자로 말하지 마세요. 대신 손상 부위 사진을 올리면
   'AI 예상 견적 받기'에서 예상 범위를 볼 수 있다고 안내하세요.
   (제품 정가는 자료에 있으면 안내해도 됩니다. 수선비와 구분해서 말하세요)
   3-1. 예외: 참고 자료에 'AI 예상 견적' 항목이 있으면, 그 범위는 그대로
        안내해도 됩니다. 단 확정가가 아니라 예상 범위임을 반드시 함께 말하고,
        자료에 적힌 숫자를 벗어난 금액은 어떤 경우에도 말하지 마세요.
4. 일반적인 가방 관리 상식이나 다른 브랜드 이야기를 섞지 마세요.
5. 다른 고객의 정보는 어떤 경우에도 언급하지 마세요.

[말투 규칙 — 어색함을 없애기 위한 것]
6. 절대 쓰지 말아야 하는 표현:
   "제가 가진 자료로", "참고 자료에 따르면", "자료에 없습니다", "데이터베이스",
   "검색 결과", "총 N건", "N건 일치", "지식베이스", "AI로서", "챗봇으로서"
   → 이건 내부 사정입니다. 손님에게 말할 내용이 아닙니다.
   "말씀하신 제품은 다섯 가지가 있어요"처럼 사람의 말로 바꾸세요.
7. **결론부터, 1~3문장.** 답을 먼저 말하고 필요한 조건만 덧붙이세요.
   서론("문의해 주셔서 감사합니다"), 되풀이("말씀하신 대로"), 사과 반복 금지.
   목록이 필요할 때만 줄바꿈으로 나열하세요.
8. 아래처럼 내용 없는 맺음말은 붙이지 마세요. 안내가 끝나면 그냥 끝내세요.
   "그렇게 해보시면 좋을 것 같아요" / "그렇게 하시면 됩니다"
   "추가로 궁금한 점이 있으시면 말씀해 주세요" / "도움이 되셨길 바랍니다"
   "언제든 문의해 주세요" / "참고하시면 좋겠습니다"
   → 이미 방법을 알려줬는데 다시 "그렇게 하세요"라고 하는 건 군더더기입니다.
9. 고객이 되물으면("여기선 못해?", "그럼 어떻게 해?") 앞 대화를 이어받아
   먼저 인정하고 다음 행동을 알려주세요.
   예: "네, 여기서는 금액까지 안내가 어려워요. 'AI 예상 견적 받기'에서 사진을 올리시면
        예상 범위를 보실 수 있어요."
   앞 답변과 똑같은 문장을 반복하지 마세요.
10. 제품이 여러 개 해당하면 하나로 단정하지 말고 후보를 보여준 뒤
    어느 제품인지 물어보세요. (겉모습이 같고 크기만 다른 제품이 실제로 있습니다)
11. '내 AS 접수' 자료가 있으면 그건 지금 상담 중인 고객의 건입니다.
    "제 가방", "언제 와요?", "지금 어디예요?" 같은 말은 그 건을 가리킵니다.
    상태·예상 완료일·진행 이력을 그 자료에서 그대로 확인해 답하세요.
    없는 단계를 지어내지 말고, 아직 안 끝난 단계는 예정이라고 말하세요.
12. 자료의 "현재 단계"와 "확정 사실" 항목이 최우선이다. 이와 어긋나게 답하지 마세요.
13. 진행 이력의 [예정] 항목 설명 문구를 지금 벌어지는 일처럼 쓰지 마세요.
14. 현재 단계가 '완료'면 모든 절차가 끝난 것입니다. 발송·배송이 남았다고 절대 말하지 마세요.
15. 자료에 없는 진행 상황을 일반적인 AS 흐름으로 추측해서 채우지 마세요.
    자료에 없으면 "아직 안내드릴 정보가 없어요. 상담원에게 확인 부탁드릴게요."라고 하세요.
16. AS와 무관한 잡담이 오면 한 문장으로만 가볍게 받고 바로 AS 주제로 돌아오세요.
    그 주제에 대해 의견·추천·감상을 덧붙이지 마세요.
    예: "재미있는 말씀이네요. AS 관련해 궁금한 점 있으실까요?" """


# ── 개인정보 마스킹 ──────────────────────────────────────────
# 고객이 채팅창에 전화번호·이메일 등을 그냥 적는 경우가 많다.
# 프롬프트로 "말하지 마"라고 부탁하는 건 방어가 아니다.
# 아예 GPT로 나가기 전에 문자열에서 지운다. (구조적 차단)
# 순서가 중요하다. 긴 것(카드·주민번호)을 먼저 지워야
# 짧은 전화번호 규칙이 카드번호 가운데를 잘라먹지 않는다.
PII_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[이메일]"),
    (re.compile(r"(?<!\d)(?:\d{4}[-\s]?){3}\d{4}(?!\d)"), "[카드번호]"),
    (re.compile(r"(?<!\d)\d{6}[-\s]?[1-4]\d{6}(?!\d)"), "[주민번호]"),
    (re.compile(r"(?<!\d)01[016-9][-\s.]?\d{3,4}[-\s.]?\d{4}(?!\d)"), "[전화번호]"),
    (re.compile(r"(?<!\d)0\d{1,2}[-\s]\d{3,4}[-\s]\d{4}(?!\d)"), "[전화번호]"),
    (re.compile(r"(?<!\d)\d{5}(?=\s*(?:번지|동|호|아파트|로|길))"), "[주소]"),
]


def redact(text):
    """에러 메시지 등에 API 키가 섞여 나오지 않게 가린다. 키 유출의 흔한 경로가 로그다."""
    return re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-***REDACTED***", text)


def mask_pii(text):
    """GPT로 보내기 전에 개인정보로 보이는 부분을 치환한다. 반환: (치환된 텍스트, 걸린 개수)"""
    hit = 0
    for pattern, tag in PII_PATTERNS:
        text, n = pattern.subn(tag, text)
        hit += n
    return text, hit


# ── mock 모드 ────────────────────────────────────────────────
# MCM_MOCK=1 이면 GPT를 부르지 않고 검색된 자료를 그대로 돌려준다.
# API 키 없이 / 비용 없이 검색·안전장치 로직만 검증할 때 쓴다.
MOCK = os.getenv("MCM_MOCK", "").strip() in ("1", "true", "True")


HISTORY_TURNS = 3      # GPT에 함께 넘길 직전 대화 수 (한 턴 = 고객 1 + 챗봇 1)
MODEL = "gpt-4o-mini"  # 모델을 바꿀 일이 생기면 여기 한 줄만 고친다


# ── 군더더기 문장 제거 ───────────────────────────────────────
# 프롬프트로 "쓰지 마"라고 해도 GPT는 종종 붙인다. 그래서 코드로 잘라낸다.
# 이미 방법을 알려준 뒤 "그렇게 해보시면 좋을 것 같아요"는 정보가 0이다.
FILLER_PATTERNS = [re.compile(p) for p in [
    r"^그렇게\s*(해\s*보시|하시|해보시)",
    r"^(그럼\s*)?그렇게\s*하시면\s*(됩니다|돼요)",
    r"좋을\s*것\s*같(아요|습니다)\s*[.!]?\s*$",
    r"^추가(로|적으로)?\s*(궁금|문의|필요)",
    r"^더\s*(궁금|필요|문의)",
    r"^다른\s*(궁금|문의|질문)",
    r"^도움이\s*(되셨|되었|되시)",
    r"^언제든\s*(문의|말씀|찾아)",
    r"^참고(하시|만\s*하시|해\s*주)",
    r"^기타\s*문의",
    r"^필요하시면\s*말씀",
    r"^(궁금한|문의할)\s*(점|사항)이?\s*(있으시)?",
]]


def _is_filler(sentence):
    """내용 없는 맺음말인지 판단한다."""
    s = sentence.strip().strip("·-•* ")
    if not s:
        return False
    return any(p.search(s) for p in FILLER_PATTERNS)


def _cut_sentence(buf):
    """
    buf에서 '완성된 문장 하나'를 잘라낸다. 아직 없으면 (None, buf).
    스트리밍 중에도 문장 단위로 검사하려면 이렇게 잘라 봐야 한다.
    """
    for i, ch in enumerate(buf):
        if ch == "\n":
            return buf[:i + 1], buf[i + 1:]
        # 문장부호 뒤에 글자가 더 와야 '끝난 문장'으로 본다.
        # (아직 스트리밍 중일 수 있으므로 버퍼 끝의 마침표는 기다린다)
        if ch in ".!?" and i + 1 < len(buf) and buf[i + 1] in " \n":
            j = i + 2 if buf[i + 1] == " " else i + 1
            return buf[:j], buf[j:]
    return None, buf


def polish(text):
    """완성된 답변에서 군더더기 문장을 걷어낸다. 다 걷히면 원문을 그대로 둔다."""
    out = []
    for line in text.split("\n"):
        if line.strip().startswith(("·", "-", "•", "*")):
            out.append(line)              # 목록 줄은 건드리지 않는다
            continue
        sents = re.split(r"(?<=[.!?])\s+", line.strip())
        kept = [s for s in sents if s.strip() and not _is_filler(s)]
        out.append(" ".join(kept))

    result = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return result or text.strip()       # 전부 걷혔으면 원문을 살린다


def polish_stream(gen):
    """스트리밍 조각을 문장 단위로 모아 군더더기만 빼고 흘려보낸다."""
    buf, emitted, dropped = "", False, []
    for piece in gen:
        buf += piece
        while True:
            sent, buf = _cut_sentence(buf)
            if sent is None:
                break
            if _is_filler(sent):
                dropped.append(sent)
            else:
                emitted = True
                yield sent
    if buf.strip():
        if _is_filler(buf) and emitted:
            dropped.append(buf)
        else:
            emitted = True
            yield buf
    # 전부 군더더기로 판정돼 아무것도 못 보냈다면 원문을 살린다 (빈 답변 방지)
    if not emitted and dropped:
        yield "".join(dropped)


def chunks(text, size):
    """
    문자열을 size글자씩 자른다. 스트리밍으로 흘려보낼 때 쓴다.

    re.S(DOTALL)가 반드시 필요하다. 정규식의 '.'은 기본적으로 줄바꿈을 매칭하지 않아서,
    이걸 빼면 줄바꿈이 조각에 안 담기고 사라진다. (실제로 목록의 줄바꿈이 다 날아갔다)
    """
    return re.findall(r".{1,%d}" % size, text, re.S)


def _prepare(question, context, history):
    """
    GPT 호출 준비를 한 곳에서 한다. (일반 호출과 스트리밍이 같이 쓴다)
    반환: (client, messages, None)  또는  (None, None, 오류메시지)
    """
    try:
        from openai import OpenAI
    except ImportError:
        return None, None, "[오류] openai 패키지가 없습니다. pip install openai 를 실행하세요."

    # 키를 복사할 때 앞뒤 공백·줄바꿈이 딸려오는 일이 잦다.
    # 그대로 두면 HTTP 헤더가 깨져 LocalProtocolError가 나는데,
    # 에러 메시지에 키 전체가 찍혀 유출된다. 그래서 여기서 미리 정리한다.
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None, None, ("[오류] OPENAI_API_KEY가 없습니다. "
                            ".env 파일을 만들거나 export 하세요. "
                            "(cp .env.example .env 후 키 입력)")
    # .env.example을 복사만 하고 값을 안 바꾼 경우. 팀원이 자주 겪는다.
    if "여기에" in api_key or "실제" in api_key or api_key == "sk-...":
        return None, None, ("[오류] .env 파일에 예시 문구가 그대로 있습니다. "
                            "OPENAI_API_KEY= 뒤를 실제 키로 바꿔주세요.")
    if not api_key.startswith("sk-"):
        return None, None, "[오류] OPENAI_API_KEY 형식이 이상합니다. 'sk-'로 시작해야 합니다."
    # strip()은 앞뒤만 지운다. 키 '가운데'에 줄바꿈·공백이 들어간 경우
    # (터미널에서 두 줄로 잘려 붙은 경우) 여기서 걸러야 한다.
    # 안 걸러면 HTTP 헤더 생성 단계에서 LocalProtocolError가 나고,
    # 겉으로는 "Connection error"로 보여 네트워크 문제로 착각하게 된다.
    if any(c.isspace() for c in api_key) or not api_key.isascii():
        return None, None, ("[오류] API 키 안에 공백·줄바꿈 또는 ASCII가 아닌 문자가 섞여 있습니다. "
                            f"(길이 {len(api_key)}) 키를 다시 복사해 export 하세요.")

    safe_question, _ = mask_pii(question)
    user_msg = f"[참고 자료]\n{context}\n\n[고객 질문]\n{safe_question}"

    # 이전 대화도 개인정보를 지운 뒤 넘긴다. 앞 턴에 적힌 전화번호가
    # 뒤 턴에서 다시 GPT로 새어나가는 걸 막는다.
    prior = []
    for m in (history or [])[-HISTORY_TURNS * 2:]:
        content = mask_pii(str(m.get("content", "")))[0]
        if content:
            prior.append({"role": m.get("role", "user"), "content": content})

    # 환경변수에 의존하지 않고 정리된 키를 직접 넘긴다.
    client = OpenAI(api_key=api_key, timeout=30.0)
    messages = ([{"role": "system", "content": SYSTEM_PROMPT}]
                + prior + [{"role": "user", "content": user_msg}])
    return client, messages, None


def ask_gpt(question, context, history=None):
    """
    참고 자료 + 직전 대화 + 질문을 보내 답변을 받는다. 실패해도 예외를 밖으로 던지지 않는다.
    history: [{"role": "user"/"assistant", "content": ...}, ...] 형태의 이전 대화
    """
    if MOCK:
        return f"[MOCK] GPT 미호출. 아래 자료를 근거로 답변할 예정입니다.\n{context}"

    client, messages, err = _prepare(question, context, history)
    if err:
        return err

    try:
        for attempt in range(3):
            try:
                res = client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    temperature=0.2,   # 낮을수록 자료에 충실하고 덜 창의적이다
                    max_tokens=400,
                )
                return polish(res.choices[0].message.content.strip())
            except Exception as e:
                # 분당 한도에 걸린 것뿐이면 잠깐 쉬고 다시 시도한다
                wait = _retry_after(e)
                if wait is None or attempt == 2:
                    raise
                print(f"[한도 초과] {wait:.0f}초 후 재시도 ({attempt + 1}/2)")
                time.sleep(wait)
    except Exception as e:
        # 네트워크 오류·한도 초과·키 오류 등. 서버가 500으로 죽지 않게 여기서 흡수한다.
        # APIConnectionError 같은 껍데기 예외는 __cause__에 진짜 이유(SSL·DNS 등)가 들어있다.
        _log_error(e)
        return SERVICE_BUSY


def ask_gpt_stream(question, context, history=None):
    """
    ask_gpt와 같은 일을 하는데, 답변을 '조각'으로 하나씩 내놓는다(제너레이터).
    → 화면에서 글자가 한 자씩 흘러나오게 만들 수 있다.

    stream=True를 주면 OpenAI가 답변을 완성해서 한 번에 주지 않고,
    만들어지는 대로 조금씩 보내준다. 그걸 그대로 브라우저로 넘긴다.
    """
    if MOCK:
        yield from chunks(f"[MOCK] GPT 미호출. 근거 자료:\n{context}", 12)
        return

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
            stream=True,
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


RETRY_MAX_WAIT = 20        # 이보다 오래 기다려야 하면 재시도를 포기한다

# 하루 한도(RPD)에 걸리면 몇십 분간 아무것도 안 된다.
# 그 상태로 테스트를 계속 돌리면 똑같은 에러만 20번 찍힌다. 그래서 기록해두고 멈춘다.
QUOTA_EXHAUSTED = {"hit": False, "message": ""}


def _retry_after(e):
    """
    429(한도 초과)일 때 몇 초 뒤 재시도하면 되는지 알아낸다.
    OpenAI가 "Please try again in 6s" 처럼 알려준다.

    분당 한도(RPM)는 몇 초만 쉬면 풀리지만,
    하루 한도(RPD)는 수십 분이라 기다릴 의미가 없다. 그래서 상한을 둔다.
    """
    msg = str(e)
    if not any(k in msg for k in ("429", "rate_limit", "Rate limit", "RateLimit")):
        return None
    # "28m48s" / "6s" / "500ms" 모두 처리한다
    m = re.search(r"try again in\s*(?:(\d+)m)?\s*([\d.]+)\s*(ms|s)\b", msg)
    if not m:
        return None
    minutes = int(m.group(1)) if m.group(1) else 0
    val, unit = float(m.group(2)), m.group(3)
    sec = minutes * 60 + (val / 1000 if unit == "ms" else val)
    if sec > RETRY_MAX_WAIT:
        # 하루 한도로 판단. 기다려도 소용없으니 기록만 남긴다.
        QUOTA_EXHAUSTED["hit"] = True
        QUOTA_EXHAUSTED["message"] = f"약 {sec / 60:.0f}분 뒤 풀립니다"
        return None
    return sec + 1


def _log_error(e):
    """예외의 겉껍데기와 진짜 원인을 모두 남긴다. API 키는 가린다."""
    print(f"[GPT 호출 실패] {redact(str(e))}  ({type(e).__name__})")
    cause, seen = e.__cause__ or e.__context__, set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        print(f"    └ 원인: {type(cause).__name__}: {redact(str(cause))}")
        cause = cause.__cause__ or cause.__context__


# ─────────────────────────────────────────────────────────────
# 5. 상담 로직 (안전장치 포함)
# ─────────────────────────────────────────────────────────────

KNOWLEDGE = AS_KNOWLEDGE + load_products()

# ── 자료가 없을 때의 답변 3종 ────────────────────────────────
# 예전엔 무슨 질문이든 "상담원 연결"로 똑같이 답했다.
# "오늘 날씨 어때?"에 상담원을 연결하겠다는 건 어색하다. 경우를 나눈다.

# "제가 가진 자료로 확인이 어렵습니다" 같은 표현은 내부 사정을 손님에게 말하는 것이라
# 챗봇 티가 난다. 무엇을 못 하는지가 아니라 어디서 되는지를 알려준다.
# AS 전담 직통번호.
# 왜 MCM 공식 고객센터(1600-1976)가 아닌가:
#   우리 서비스는 AS 상담에 특화돼 있고, 화면 푸터에도 AS 전담 번호가 따로 있다.
#   챗봇이 화면과 다른 번호를 말하면 시연에서 바로 눈에 띈다.
AGENT_TEL = "1588-0001"

NO_ANSWER = (f"이 부분은 상담원이 직접 확인해 드리는 게 정확해요. "
             f"고객센터 {AGENT_TEL}으로 문의해 주시겠어요?")

# API 장애·한도 초과처럼 '일시적으로' 답을 못 만든 경우.
# NO_ANSWER와 같은 문장을 쓰면 "자료가 없어서 못 답한 것"과 구분이 안 된다.
# 실제로 한도에 걸렸을 때 자료가 멀쩡히 실렸는데도 상담원 안내가 나가 헷갈렸다.
SERVICE_BUSY = ("죄송해요, 지금 답변을 준비하는 데 문제가 생겼어요. "
                "잠시 후 다시 여쭤봐 주시겠어요? "
                f"급하시면 고객센터 {AGENT_TEL}으로 문의하셔도 됩니다.")

# 문의 종류별로 다르게 답한다. 같은 문장을 돌려쓰면 금방 기계처럼 느껴진다.
AGENT_MESSAGES = [
    (["매장", "지점", "백화점", "면세점", "위치", "어디에 있"],
     "매장 위치는 제가 안내드리기 어려워요. "
     "MCM 공식 홈페이지의 매장 찾기에서 확인하실 수 있고, "
     f"고객센터 {AGENT_TEL}으로 문의하셔도 됩니다."),
    (["분실", "도난", "잃어", "없어졌"],
     "분실이나 도난은 확인이 필요한 부분이라 상담원이 직접 도와드려야 해요. "
     f"고객센터 {AGENT_TEL}으로 연락 부탁드립니다."),
    (["보상", "배상", "클레임", "컴플레인", "항의", "소송"],
     "말씀하신 내용은 담당자가 직접 확인해 드리는 게 맞겠어요. "
     f"고객센터 {AGENT_TEL}으로 연결해 드릴까요?"),
]

# 우리가 정말 답할 수 없는 것 → 상담원 연결이 맞다.
# 8/12: 환불·반품·교환·영업시간은 MCM 공식 자료를 확보해 지식베이스로 옮겼다.
#       그래서 여기서 뺐다. 이제 챗봇이 직접 답한다.
REFER_TO_AGENT = ["보상", "배상", "분실", "도난", "잃어버", "클레임", "컴플레인",
                  "항의", "소송", "법적", "매장", "지점", "백화점", "면세점"]

# 위 단어가 있어도 상담원으로 넘기면 안 되는 경우.
#   "보증서 잃어버렸는데 접수 되나요?" → 제품 분실이 아니라 서류 문의다.
#   여기 걸리면 정상 검색으로 보낸다.
REFER_EXCEPTIONS = ["보증서", "워런티", "영수증", "카드", "더스트백", "택", "tag",
                    "주문번호", "구성품", "박스", "케이스"]

# AS 서비스 얘기는 맞는데 딱 맞는 자료를 못 찾은 경우 → 기본 안내로 GPT가 답하게 한다
SERVICE_HINTS = ["as", "케어", "수선", "수리", "고치", "맡기", "가방", "제품", "상품",
                 "백팩", "지갑", "파우치", "크로스", "손상", "가죽", "캔버스",
                 "접수", "픽업", "수거", "배송", "견적", "비용", "기간", "관리", "보관",
                 # 2026-08-13 보강: 상품을 가방·지갑에서 13개 카테고리 전체로 늘렸다.
                 # 품목 단어가 없으면 "벨트 버클이 망가졌어요"가 범위 밖으로 빠진다.
                 "벨트", "스카프", "신발", "슈즈", "스니커즈", "로퍼", "모자", "캡",
                 "키링", "참", "의류", "재킷", "티셔츠", "후드", "카드지갑", "토트",
                 "숄더", "호보", "슬링", "클러치", "캐리어", "여행"]

# 위에 해당할 때 GPT에 기본으로 넘기는 자료
DEFAULT_TOPICS = ["접수 방법", "소요 기간", "견적 및 비용"]

OUT_OF_SCOPE = (
    "제가 도와드릴 수 있는 건 MCM 제품의 AS 쪽이에요.\n"
    "예를 들면 이런 것들을 바로 안내드릴 수 있어요.\n"
    "· 수선 접수는 어떻게 하는지, 무엇을 준비해야 하는지\n"
    "· 수선이 얼마나 걸리는지\n"
    "· 제품을 어떻게 보관하고 관리하면 되는지\n"
    "· 제품의 소재나 크기"
)

GREETING_RE = re.compile(r"^(안녕|하이|헬로|hi|hello|반가|고마|감사|땡큐|thank|"
                         r"수고|잘\s*있어|안녕히|바이|bye|ㅎㅇ|ㅋㅋ+|ㅎㅎ+)")
GREETING = "안녕하세요, Custodia입니다. 어떤 점이 궁금하신가요?"
THANKS_RE = re.compile(r"^(고마|감사|땡큐|thank|수고|잘\s*있어|안녕히|바이|bye)")
THANKS = "도움이 되었다면 다행이에요. 필요할 때 언제든 다시 찾아주세요."


def _agent_message(q):
    """문의 종류에 맞는 상담원 안내 문구를 고른다. 없으면 기본 문구."""
    for words, msg in AGENT_MESSAGES:
        if any(w in q for w in words):
            return msg
    return NO_ANSWER


def _no_hit(question):
    """
    자료를 못 찾았을 때 어떻게 할지 정한다.
    반환: (미리 써둔 답변, 기본 자료 목록) — 둘 중 하나만 채워진다.

    예전엔 무조건 "상담원 연결"이었다. 그런데 "내 가방을 수선 맡기고 싶은데"처럼
    분명 AS 얘기인데 키워드가 안 걸린 경우까지 상담원으로 보내버려 대화가 끊겼다.
    """
    q = question.lower()

    # 인사·환불류는 _retrieve 앞단에서 이미 걸러진 상태로 들어온다.
    # AS 얘기는 맞으니 기본 안내(접수 방법·기간·견적)를 주고 GPT가 이어받게 한다
    if any(w in q for w in SERVICE_HINTS):
        return None, [k for k in AS_KNOWLEDGE if k["topic"] in DEFAULT_TOPICS]

    # 완전히 범위 밖 → 무엇을 할 수 있는지 안내
    return OUT_OF_SCOPE, []


# 수선 비용을 묻는 말인지 판단한다. 견적 조회는 비싼 작업이라 이때만 부른다.
# "얼마나"는 기간·정도를 묻는 말이라 먼저 지운다. ("얼마나 남았어요?" = 배송 문의)
MONEY_WORDS = ("얼마", "비용", "가격", "값", "견적", "수선비", "요금", "금액",
               "돈", "무상", "유상", "보증", "청구", "결제", "지불")


def _asks_about_money(question):
    return any(w in (question or "").replace("얼마나", "") for w in MONEY_WORDS)


def _retrieve(question, history=None, as_id=None):
    """
    검색까지만 담당한다. (일반 답변과 스트리밍 답변이 이 함수를 같이 쓴다)
    as_id: 상담 중인 AS 접수번호. 있으면 그 건 정보를 항상 자료에 넣는다.
    반환: {question, context, sources, masked, used_history, fallback}
      fallback이 채워져 있으면 GPT를 부르지 않고 그 문장을 그대로 쓴다.
    """
    question = (question or "").strip()
    base = {"question": question, "context": "", "sources": [],
            "masked": 0, "used_history": False, "fallback": None}

    # 접수 건은 '검색해서 찾는' 게 아니라 '항상 들고 있는' 자료다.
    # 고객이 "내 가방"이라고만 해도 무엇인지 알아야 하기 때문.
    repair = find_repair(as_id)
    pinned = [repair_knowledge(repair)] if repair else []

    # 접수번호를 받았는데 못 불러온 경우, 그 사실 자체를 자료로 넣는다.
    # 안 넣으면 GPT 가 "접수 건이 없다"고 단정하거나 일반 안내로 얼버무린다.
    if as_id and not repair:
        why = lookup_failure()
        if why == "error":
            pinned.append({
                "topic": "접수 건 조회 실패",
                "keywords": [], "source": "시스템",
                "content": ("지금 접수 내역 시스템에 연결되지 않아 이 고객의 접수 건을 "
                            "불러오지 못했다. 진행 상황·예상 완료일·견적은 답하지 말고, "
                            "일시적인 문제라 잠시 후 다시 확인해 달라고 안내한다. "
                            "접수 절차·제품 관리 같은 일반 문의는 평소대로 답한다."),
            })
        elif why == "not_found":
            pinned.append({
                "topic": "접수 건 조회 실패",
                "keywords": [], "source": "시스템",
                "content": (f"'{as_id}' 접수 건을 찾지 못했다. 없는 번호이거나 "
                            "이 고객의 접수 건이 아니다. 진행 상황을 지어내지 말고 "
                            "접수번호를 다시 확인해 달라고 안내한다."),
            })

    # 이 고객의 AI 예상 견적 결과가 서버에 있으면 함께 넣는다.
    # 왜: 견적이 이미 나온 건인데도 "견적 받아보세요"라고 답하면 앞뒤가 안 맞는다.
    # 자료가 있을 때만 금액을 말하고, 없으면 지금까지처럼 견적 화면으로 유도한다.
    #
    # ⚠️ 돈 얘기가 나올 때만 부른다.
    # 서버의 견적 조회는 저장된 값을 읽는 게 아니라 그 시점에 AI 모델을 다시 돌린다
    # (AsCaseFacade: "estimate 테이블이 없으므로 조회 시점에 재계산한다").
    # 매 턴 부르면 말 한 마디마다 모델 추론이 도는 셈이라, 답이 느려지고 서버도 부담된다.
    if repair and _asks_about_money(question) \
            and spring_client is not None and spring_client.is_enabled():
        est = spring_client.fetch_estimate(repair["as_id"])
        if est:
            pinned.append(est)

    if not question:
        return {**base, "fallback": "무엇을 도와드릴까요?"}

    _, masked = mask_pii(question)      # 몇 건이 가려졌는지 기록용 (로그·검증)
    base["masked"] = masked

    q = question.lower()
    if GREETING_RE.match(q.strip()):
        # 첫인사와 마무리 인사를 구분한다. "고마워요"에 "안녕하세요"는 어색하다.
        return {**base, "fallback":
                THANKS if THANKS_RE.match(q.strip()) else GREETING}

    # 환불·반품·매장 등은 검색 전에 걸러낸다.
    # 뒤로 흘려보내면 "반품하고싶어"의 '하고싶'이 접수 안내에 걸려서
    # 반품 문의에 AS 접수 절차를 안내하는 엉뚱한 답이 나온다.
    if any(w in q for w in REFER_TO_AGENT) and \
            not any(w in q for w in REFER_EXCEPTIONS):
        return {**base, "fallback": _agent_message(q)}

    hits = search(question, KNOWLEDGE)
    used_history = False

    # "12CO001이요", "그거 얼마예요?" 같은 후속 발화는 그 자체로는 검색이 안 된다.
    # ("그거"가 무엇인지는 앞 대화에만 있다)
    # 그래서 상품 자료가 안 잡혔을 때만 직전 고객 발화를 붙여 한 번 더 찾는다.
    # 처음부터 붙이지 않는 이유: 새 주제로 넘어갔는데 이전 주제가 계속 따라붙는다.
    # 이전 대화를 붙여 재검색할지 판단한다.
    # 아무 질문에나 붙이면, 대화 도중 "오늘 날씨 어때?"를 물어도
    # 앞 대화의 상품 자료가 딸려와 범위 안내로 못 빠진다. (실제로 그랬다)
    # 그래서 '앞 얘기에 이어지는 말'로 보일 때만 붙인다.
    followup = (
        any(w in q for w in ("그거", "그건", "이거", "저거", "그때", "아까",
                             "여기", "거기", "방금", "말한", "위에"))
        or any(w in q for w in PRODUCT_QUERY_WORDS)
        or any(w in q for w in SERVICE_HINTS)
        or bool(re.search(r"[0-9A-Za-z]{4,}", question))   # 상품코드 일부
    )

    if followup and history and not any(h["topic"].startswith("상품") for h in hits):
        prev = " ".join(m.get("content", "") for m in history
                        if m.get("role") == "user")[-200:]
        if prev.strip():
            merged = search(f"{prev} {question}", KNOWLEDGE)
            # 1차에서 아무것도 못 찾았으면 전부 받아들인다.
            #   ("여기선 못해?" 처럼 앞 질문에 딸린 되묻기가 여기 해당)
            # 1차에서 AS 안내는 찾았지만 상품이 없으면 상품만 보탠다.
            extra = merged if not hits else [
                h for h in merged
                if h["topic"].startswith("상품") or "검색 요약" in h["topic"]]
            if extra:
                seen = {h["topic"] for h in hits}
                hits += [h for h in extra if h["topic"] not in seen][:3]
                used_history = True

    # 관련 자료가 하나도 없으면 GPT를 부르지 않는다.
    # → GPT가 자기 지식으로 지어낼 여지를 아예 없앤다. (비용도 아낀다)
    # 단, 상담 중인 접수 건이 있으면 그것만으로도 답할 거리가 있으므로 GPT를 부른다.
    if not hits and not pinned:
        canned, default_hits = _no_hit(question)
        if canned is not None:
            return {**base, "fallback": canned}
        hits = default_hits         # 기본 안내를 주고 GPT가 이어받는다

    all_hits = pinned + hits        # 접수 건을 항상 맨 앞에 둔다
    return {**base,
            "context": "\n\n---\n\n".join(f"[{h['topic']}]\n{h['content']}"
                                          for h in all_hits),
            "sources": [h["topic"] for h in all_hits],
            "used_history": used_history}


def answer(question, history=None, as_id=None):
    """
    질문 하나에 대한 답변을 만든다.
    history: 이전 대화 [{"role": "user"/"assistant", "content": ...}, ...]
    as_id:   상담 중인 AS 접수번호 (있으면 개인화된 답변)
    반환: {answer, sources, used_gpt, masked, used_history}
    """
    r = _retrieve(question, history, as_id)
    if r["fallback"] is not None:
        return {"answer": r["fallback"], "sources": [], "used_gpt": False,
                "masked": r["masked"], "used_history": False}
    return {
        "answer": ask_gpt(r["question"], r["context"], history),
        "sources": r["sources"],
        "used_gpt": not MOCK,
        "masked": r["masked"],
        "used_history": r["used_history"],
    }


def log_turn(question, r, seconds=None, as_id=None):
    """
    상담 한 건을 로그 한 줄로 남긴다. 시연 중 이상한 답이 나오면 여기부터 본다.

    질문은 개인정보를 지운 뒤 찍는다. 로그 파일도 유출 경로이기 때문이다.
    답변 전문은 찍지 않는다. 길어서 로그가 안 읽히고, 개인정보가 섞일 수 있다.
    """
    safe, masked = mask_pii(question or "")
    if len(safe) > 40:
        safe = safe[:40] + "…"

    parts = [f'Q "{safe}"']
    if as_id:
        parts.append(f"as_id={as_id}")
    parts.append(f"근거 {len(r['sources'])}건: " +
                 (", ".join(r["sources"][:3]) or "없음"))
    if masked:
        parts.append(f"개인정보 {masked}건 가림")
    if not r["used_gpt"]:
        parts.append("GPT 미호출")
    if seconds is not None:
        parts.append(f"{seconds:.1f}초")

    print("[상담] " + " | ".join(parts))


def opening_message(as_id):
    """
    상담 시작 시 챗봇이 먼저 건네는 인사말.
    기획 목업의 첫 메시지를 그대로 재현한다. GPT를 부르지 않으므로 항상 정확하다.
    """
    repair = find_repair(as_id)
    if not repair:
        # as_id 를 줬는데 못 찾았으면 그 사실을 말한다.
        # 조용히 일반 인사말로 넘어가면 고객은 자기 건을 보고 있다고 착각한다.
        why = lookup_failure() if as_id else ""
        if as_id and why == "error":
            return ("안녕하세요, Custodia AI 컨시어지입니다.\n"
                    "접수 내역을 불러오는 데 일시적으로 문제가 있어요. "
                    "잠시 후 다시 시도해 주세요. 그 밖의 문의는 지금도 도와드릴 수 있습니다.")
        if as_id and why == "not_found":
            return ("안녕하세요, Custodia AI 컨시어지입니다.\n"
                    f"{as_id} 접수 건을 찾지 못했습니다. 접수번호를 다시 확인해 주시겠어요? "
                    "접수 절차나 제품 관리에 대해서는 지금도 도와드릴 수 있습니다.")
        return ("안녕하세요, Custodia AI 컨시어지입니다.\n"
                "수선 접수나 제품 관리에 대해 궁금한 점을 편하게 물어보세요.")

    # 손상 표현.
    #
    # damage_description 을 먼저 쓰던 것을 바꿨다.
    # 고객이 자유롭게 쓴 문장이라 "사용 중 벌어졌습니다" 처럼 종결어미가 붙어 있고,
    # 그대로 넣으면 "백팩 — 사용 중 벌어졌습니다 건이" 가 되어 문장이 깨진다.
    # 유형·부위는 명사라 문장에 넣어도 자연스럽다.
    #   유형("금속부품손상") > 부위("메인 수납부 지퍼") > "수선"
    damage = (repair.get("damage_type")
              or repair.get("damage_part")
              or "수선")

    # 호칭. Spring 이 붙어 있으면 로그인한 고객 이름을 쓴다.
    # 없으면 이름 없이 간다 — 남의 이름(더미 고객)을 부르는 것보다 낫다.
    me = None
    if spring_client is not None and spring_client.is_enabled():
        me = spring_client.fetch_member()
    who = (me or CUSTOMER or {}).get("name")
    call = f"{who} 고객님, " if who else ""

    line = (f"{call}{repair.get('as_id')} 건을 확인했습니다. "
            f"{repair.get('product_name') or '접수하신 제품'} — {damage} 건이 "
            f"현재 '{repair.get('stage') or '확인 중'}' 단계입니다.")
    if repair.get("expected_at"):
        gkey = _guide_key(repair)
        if gkey == "COMPLETED":
            line += f" 완료일은 {repair['expected_at']}입니다."
        else:
            line += f" 예상 완료일은 {repair['expected_at']}입니다."
    return "안녕하세요, Custodia AI 컨시어지입니다.\n" + line + " 궁금하신 점을 말씀해 주세요."


# ─────────────────────────────────────────────────────────────
# 6. FastAPI 서버
# ─────────────────────────────────────────────────────────────

try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, StreamingResponse
    from pydantic import BaseModel

    app = FastAPI(title="Custodia - MCM AS AI 상담", version="0.3.0")

    # 프론트(세희·진성)가 다른 주소에서 개발 서버를 띄워도 호출할 수 있게 허용한다.
    # 브라우저는 기본적으로 '다른 주소로의 요청'을 막는데(CORS), 이 설정이 그 빗장을 푼다.
    # allow_origins=["*"]는 개발용이다. 배포할 땐 실제 도메인만 남겨야 한다.
    # 배포할 때 코드를 고치지 않아도 되게 환경변수로 뺀다.
    #   ALLOWED_ORIGINS=https://front.vercel.app,https://mcmcare.kr
    # 비워두면 개발용으로 전부 허용한다.
    _origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if _origins:
        print(f"[CORS] 허용 주소 {len(_origins)}개: {', '.join(_origins)}")
    else:
        print("[CORS] 전체 허용(개발용). 배포 시 ALLOWED_ORIGINS를 설정하세요.")

    @app.middleware("http")
    async def _pass_through_token(request, call_next):
        """
        프론트가 보낸 Authorization 헤더를 그대로 광은님 서버 호출에 물려준다.

        왜: 광은님 서버는 JWT로 '누구인지'를 판별한다. 챗봇이 고정 토큰 하나만 쓰면
            모든 고객이 같은 사람으로 조회돼 남의 접수 건이 보일 수 있다.
            헤더가 없으면 기존대로 SPRING_API_TOKEN(또는 더미)으로 동작한다.
        """
        if spring_client is not None:
            spring_client.set_request_token(request.headers.get("authorization", ""))
        return await call_next(request)

    @app.get("/", include_in_schema=False)
    def home():
        """브라우저로 접속하면 채팅 화면을 준다. (chat.html)"""
        page = os.path.join(BASE_DIR, "chat.html")
        if os.path.exists(page):
            return FileResponse(page)
        return {"안내": "chat.html이 없습니다. API 문서는 /docs 에서 보세요."}

    # 대화 이력 저장소.
    # 데모용으로 '서버 메모리'에만 둔다 → 서버를 끄면 사라진다.
    # 실서비스에서는 DB나 Redis로 옮겨야 한다. (지금 단계에서 필요 없음)
    # 대화 이력.
    #
    # [왜 파일에도 저장하는가 — 2026-08-13]
    # 무료 배포 플랜(Render 등)은 15분간 요청이 없으면 서버를 재웠다가
    # 다음 요청에 다시 띄운다. 그때 프로세스가 새로 뜨므로 메모리는 비워진다.
    # 심사 기간(8/21~24)에 심사위원이 띄엄띄엄 질문하면 매번 맥락이 끊긴다.
    # 그래서 메모리를 그대로 쓰되, 바뀔 때마다 파일에 한 벌 써둔다.
    #
    # DB를 쓰지 않는 이유: 기획상 상담 내역은 영구 저장하지 않기로 했다(Frame 718).
    # 이건 '영구 보관'이 아니라 '재시작을 견디는 임시 저장'이다.
    def _session_key(sid):
        """
        대화 세션 키. 클라이언트가 보낸 값을 그대로 쓰지 않는다.

        [왜]
        전에는 session_id 를 그대로 키로 썼다. 회원 A 가 "m5" 를 보내면
        5번 회원의 대화 기록이 딸려왔다 — 남의 상담 내용이 보이는 것이다.
        토큰 해시를 앞에 붙이면 같은 "m5" 라도 사람마다 다른 방이 된다.

        토큰이 없으면(로컬 테스트) 예전처럼 동작한다.
        """
        if not sid:
            return None
        token = ""
        if spring_client is not None:
            try:
                token = spring_client.current_token()
            except Exception:
                token = ""
        if not token:
            return sid
        return hashlib.sha256(token.encode()).hexdigest()[:12] + ":" + sid

    SESSIONS: dict[str, list] = {}
    MAX_SESSIONS = 500          # 메모리가 무한정 늘지 않게 상한을 둔다
    SESSION_FILE = os.getenv("SESSION_FILE", os.path.join(BASE_DIR, ".sessions.json"))

    # 대화를 디스크에 남길지.
    #
    # [왜 기본값이 꺼짐인가]
    # 기획상 상담 내역은 영구 저장하지 않기로 했다(Frame 718).
    # 그리고 고객이 대화 중에 전화번호·주소를 적으면 그게 그대로 파일에 남는다.
    # GPT 로 보낼 때는 가리는데 디스크에는 원문이 쓰이고 있었다 — 앞뒤가 안 맞는다.
    #
    # 재시작을 견뎌야 하는 상황(로컬 개발 중 --reload)에서만 켠다.
    #   SESSION_PERSIST=1 uvicorn ...
    SESSION_PERSIST = os.getenv("SESSION_PERSIST", "0") == "1"

    def _load_sessions():
        """서버가 뜰 때 이전 대화를 복구한다. 실패해도 그냥 빈 상태로 시작한다."""
        if not SESSION_PERSIST:
            return
        try:
            if os.path.exists(SESSION_FILE):
                with open(SESSION_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    SESSIONS.update(data)
                    print(f"[세션] 이전 대화 {len(SESSIONS)}건 복구")
        except Exception as e:
            print(f"[세션] 복구 실패(무시하고 계속): {e}")

    def _save_sessions():
        """대화가 바뀔 때마다 파일에 쓴다. 실패해도 상담은 계속되어야 한다."""
        if not SESSION_PERSIST:
            return
        try:
            tmp = SESSION_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(SESSIONS, f, ensure_ascii=False)
            os.replace(tmp, SESSION_FILE)      # 쓰다 만 파일이 남지 않게 통째로 교체
        except Exception as e:
            print(f"[세션] 저장 실패(무시하고 계속): {e}")

    _load_sessions()

    class ChatRequest(BaseModel):
        message: str
        session_id: str | None = None   # 같은 값을 계속 보내면 대화가 이어진다
        as_id: str | None = None        # 상담 중인 AS 접수번호 (개인화)

        model_config = {"json_schema_extra": {
            "examples": [{"message": "제 가방 지금 어디에 있어요?",
                          "session_id": "demo-1",
                          "as_id": "AS-2026-00871"}]}}

    class ChatResponse(BaseModel):
        answer: str
        sources: list[str]      # 어떤 자료를 근거로 답했는지 (검증용)
        used_gpt: bool
        masked: int = 0         # 개인정보로 판단해 가린 항목 수
        used_history: bool = False   # 이전 대화를 참고해 찾았는지
        session_id: str | None = None

    @app.post("/chat", response_model=ChatResponse, summary="AI 상담")
    def chat(req: ChatRequest):
        """
        AS 접수 절차, 소요 기간, 상품 소재·관리 관련 질문에 답변합니다.

        - 자료에 없는 내용은 답하지 않고 상담원 연결을 안내합니다
        - 수선 금액은 안내하지 않고 'AI 예상 견적'으로 유도합니다
        - `session_id`를 같이 보내면 이전 대화를 기억합니다
          (예: "치수 알려줘" → "5건입니다" → "12CO001이요" → 해당 상품 치수)
        """
        sid = req.session_id
        key = _session_key(sid)
        history = SESSIONS.get(key, []) if key else []

        started = time.perf_counter()
        result = answer(req.message, history, req.as_id)
        log_turn(req.message, result, time.perf_counter() - started, req.as_id)

        if sid:
            if key not in SESSIONS and len(SESSIONS) >= MAX_SESSIONS:
                SESSIONS.pop(next(iter(SESSIONS)))       # 가장 오래된 것부터 버린다
            turns = SESSIONS.setdefault(key, [])
            turns.append({"role": "user", "content": req.message})
            turns.append({"role": "assistant", "content": result["answer"]})
            del turns[:-HISTORY_TURNS * 2]               # 최근 N턴만 남긴다
            _save_sessions()
        return {**result, "session_id": sid}

    @app.post("/chat/stream", summary="AI 상담 (실시간 스트리밍)")
    def chat_stream(req: ChatRequest):
        """
        `/chat`과 같지만 답변이 **만들어지는 대로** 조금씩 전송됩니다.
        화면에서 글자가 한 자씩 나타나게 하려면 이쪽을 쓰세요.

        전송 형식은 SSE(Server-Sent Events). 한 줄씩 아래 JSON이 옵니다.
          {"type":"meta",  "sources":[...], "used_gpt":true, "masked":0, ...}
          {"type":"delta", "text":"수선 "}      ← 여러 번 반복
          {"type":"done"}
        """
        sid = req.session_id
        key = _session_key(sid)
        history = list(SESSIONS.get(key, [])) if key else []
        r = _retrieve(req.message, history, req.as_id)

        def event_stream():
            started = time.perf_counter()
            used_gpt = bool(r["fallback"] is None and not MOCK)
            meta = {"type": "meta", "sources": r["sources"],
                    "used_gpt": used_gpt,
                    "masked": r["masked"], "used_history": r["used_history"],
                    "session_id": sid}
            yield "data: " + json.dumps(meta, ensure_ascii=False) + "\n\n"

            full = ""
            if r["fallback"] is not None:
                # 미리 준비된 문장. 그래도 한 번에 튀어나오지 않게 조각내 보낸다.
                for part in chunks(r["fallback"], 6):
                    full += part
                    yield ("data: " + json.dumps({"type": "delta", "text": part},
                                                 ensure_ascii=False) + "\n\n")
                    time.sleep(0.02)
            else:
                # polish_stream: 문장이 완성될 때마다 군더더기인지 보고 걸러 보낸다
                for part in polish_stream(
                        ask_gpt_stream(r["question"], r["context"], history)):
                    full += part
                    yield ("data: " + json.dumps({"type": "delta", "text": part},
                                                 ensure_ascii=False) + "\n\n")

            # 대화 저장은 답변이 끝난 뒤에 한다
            if sid:
                if key not in SESSIONS and len(SESSIONS) >= MAX_SESSIONS:
                    SESSIONS.pop(next(iter(SESSIONS)))
                turns = SESSIONS.setdefault(key, [])
                turns.append({"role": "user", "content": req.message})
                turns.append({"role": "assistant", "content": full})
                del turns[:-HISTORY_TURNS * 2]
                _save_sessions()

            log_turn(req.message,
                     {"sources": r["sources"], "used_gpt": used_gpt},
                     time.perf_counter() - started, req.as_id)
            yield "data: " + json.dumps({"type": "done"}, ensure_ascii=False) + "\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            # 중간 서버(nginx 등)가 응답을 모아두면 스트리밍이 무의미해진다. 끄라고 알린다.
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── AS 접수 조회 (프론트의 '나의 AS 목록 / 상세' 화면용) ──
    # 지금은 더미 JSON을 읽는다. 실서비스에서는 DB 조회로 바뀐다.

    @app.get("/repairs", summary="나의 AS 목록")
    def repair_list():
        """로그인한 고객의 AS 접수 목록을 최근 순으로 돌려줍니다."""
        return {
            # '_'로 시작하는 키는 파일에 적어둔 내부 메모다. 밖으로 내보내지 않는다.
            # (as_dummy.json에 "_membership_주의" 같은 설명을 적어두기 때문)
            "customer": {k: v for k, v in CUSTOMER.items() if not k.startswith("_")},
            "count": len(REPAIRS),
            "repairs": [
                {"as_id": r["as_id"], "product_name": r["product_name"],
                 "product_code": r["product_code"], "stage": r["stage"],
                 "received_at": r["received_at"], "expected_at": r["expected_at"],
                 "damage_type": r.get("damage_type")}
                for r in sorted(REPAIRS, key=lambda x: x["received_days_ago"])
            ],
        }

    @app.get("/repairs/{as_id}", summary="AS 접수 상세")
    def repair_detail(as_id: str):
        """접수번호 하나의 상세 정보와 진행 이력을 돌려줍니다."""
        r = find_repair(as_id)
        if not r:
            return {"error": "해당 접수번호를 찾을 수 없습니다.", "as_id": as_id}
        return r

    @app.get("/chat/opening", summary="상담 시작 인사말")
    def opening(as_id: str | None = None):
        """
        상담 화면을 열 때 먼저 보여줄 문장입니다.
        GPT를 부르지 않으므로 항상 정확하고 즉시 응답합니다.
        """
        msg = opening_message(as_id)
        return {"answer": msg, "message": msg, "as_id": as_id}

    @app.delete("/chat/{session_id}", summary="대화 초기화")
    def reset(session_id: str):
        """시연 중 처음부터 다시 시작할 때 사용합니다."""
        # 삭제도 같은 규칙으로 키를 만든다. 남의 세션을 지울 수 없다.
        gone = SESSIONS.pop(_session_key(session_id), None) is not None
        if gone:
            _save_sessions()
        # 서버 조회 캐시도 함께 비운다.
        # 시연 중 어드민에서 상태를 바꾸고 다시 물어볼 때 옛 값이 남으면 안 되기 때문.
        if spring_client is not None:
            spring_client.clear_cache()
        return {"deleted": gone}

    @app.get("/health", summary="서버 상태 확인")
    def health():
        return {
            "status": "ok",
            "지식베이스": len(KNOWLEDGE),
            "AS안내": len(AS_KNOWLEDGE),
            "상품": len(KNOWLEDGE) - len(AS_KNOWLEDGE),
            "mock모드": MOCK,
            "API키설정됨": bool(os.getenv("OPENAI_API_KEY")),
            "Spring연동": bool(spring_client and spring_client.is_enabled()),
            "진행중대화": len(SESSIONS),
        }

except ImportError:
    app = None      # fastapi가 없어도 터미널 모드는 동작하게


# ─────────────────────────────────────────────────────────────
# 7. 터미널에서 바로 테스트 (서버 없이)
# ─────────────────────────────────────────────────────────────

TEST_CASES = [
    # (질문, 기대 동작)
    ("수선 얼마나 걸려요?", "소요 기간 안내"),
    ("내 가방을 수선 맡기고 싶은데", "접수 방법 안내 (상담원으로 빠지면 실패)"),
    ("가방 지퍼가 고장났어요", "손상 접수 안내"),
    ("AS 접수하려면 뭐 준비해요?", "접수 준비물 안내"),
    ("가방에 물이 묻었어요 어떻게 해요?", "제품 관리법 안내"),
    ("수선비 얼마예요?", "금액 직답 금지 → AI 예상 견적 유도"),
    ("비세토스 캔버스 관리법 알려줘", "관리법 + 관련 상품"),
    ("MMKEAVE12CO001 크기가 어떻게 되나요", "해당 상품 치수 16 x 33 x 41"),
    ("비세토스 백팩 얼마에요?", "여러 건 → 개수·가격범위 안내 + 상품명 되묻기 (건수는 데이터에 따라 변함)"),
    ("사이드 스터드 백팩 치수 알려줘", "여러 건 → 단정 금지 (12CO001/14CO001 치수 다름)"),
    ("환불해주세요", "15일 이내 미사용 조건 안내 (MCM 공식 기준)"),
    ("반품하면 배송비 누가 내요?", "단순변심 시 고객 부담 · CJ 왕복 5천원"),
    ("수선 끝나면 어떻게 받아요?", "접수 시 선택한 반환 방법 · 운송장 문자 안내"),
    ("고객센터 몇 시까지 해요?", "평일 10~19시, 주말·공휴일 제외"),
    ("매장 어디 있어요?", "자료 없음 → 매장 찾기 안내 (GPT 미호출)"),
    ("오늘 날씨 어때?", "범위 밖 → 할 수 있는 일 안내 (상담원 연결 아님)"),
    ("안녕하세요", "인사 → 짧은 인사말"),
    ("제 번호는 010-1234-5678, 메일은 a@b.com 입니다", "개인정보 마스킹 2건"),
]


# 답변에 나오면 안 되는 내부 표현. 손님에게 할 말이 아니다.
LEAK_WORDS = ["제가 가진 자료", "참고 자료", "자료에 없", "자료에 따르", "지식베이스",
              "데이터베이스", "검색 결과", "검색 요약", "건 일치", "AI로서", "챗봇으로서",
              "GPT", "프롬프트", "컨텍스트"]


def _flag(question, expect, r):
    """자동으로 잡을 수 있는 것만 기계가 잡는다. 나머지는 사람이 읽고 판단한다."""
    a = r["answer"]
    warn = []
    # API가 실패한 경우. 로직 문제가 아니므로 답변 품질을 판단할 수 없다.
    if a == SERVICE_BUSY:
        return ["API 호출 실패 — 답변 품질 판단 불가 (로직 문제 아님)"]
    # 내부 표현이 답변에 새어나왔는지 (말투 어색함의 주된 원인)
    # MOCK 모드는 답변 자리에 자료 원문을 그대로 찍으므로 이 검사를 건너뛴다.
    if not MOCK:
        for w in LEAK_WORDS:
            if w in a:
                warn.append(f"내부 표현 노출: '{w}'")
        if re.search(r"총\s*\d+건", a):
            warn.append("'총 N건' 표현 — 사람 말투로 바꿔야 함")
    # 수선 금액을 직접 말했는지 (안전장치 2). '정가/판매가' 문맥은 제외.
    if "수선" in question or "견적" in question or "수선비" in question:
        for m in re.finditer(r"([\d,]{4,})\s*원", a):
            near = a[max(0, m.start() - 15):m.end()]
            if not any(w in near for w in ("정가", "판매가", "제품")):
                warn.append(f"금액 직답 의심: {m.group(0)}")
    # 여러 건인데 하나로 단정했는지.
    # 단, '관리법' 같은 질문은 상품이 여러 건 걸려도 답이 하나뿐이라 건수를 밝힐 이유가 없다.
    # (그 경우까지 경고하면 오탐이 되어 경고 자체를 무시하게 된다)
    asks_per_product = any(w in question.replace("얼마나", "") for w in
                           ("얼마", "가격", "치수", "크기", "사이즈", "종류", "목록", "뭐가 있"))
    if asks_per_product and any("검색 요약" in s for s in r["sources"]):
        cnt = re.search(r"(\d+)건", " ".join(r["sources"]))
        # 숫자 그대로("5") 또는 한글("다섯", "5가지") 중 하나라도 있으면 통과
        korean = {"2": "두", "3": "세", "4": "네", "5": "다섯", "6": "여섯",
                  "7": "일곱", "8": "여덟", "9": "아홉", "10": "열"}
        if cnt:
            n = cnt.group(1)
            if n not in a and korean.get(n, "\0") not in a:
                warn.append(f"제품 개수({n}개) 미고지")
    return warn


# 연속 대화 테스트. 앞 질문의 답을 기억해야만 통과한다.
MULTITURN_CASES = [
    ["사이드 스터드 백팩 치수 알려줘", "12CO001이요"],
    ["MMKEAVE12CO001 크기 알려줘", "그거 얼마예요?"],
    ["가방 수선 맡기고 싶은데 얼마나 걸릴까?", "가격은?", "여기선 못해?"],
]

# 접수 건을 알고 있을 때만 답할 수 있는 질문들 (개인화 검증용)
PERSONAL_CASES = [
    ("AS-2026-00871", "제 가방 지금 어디에 있어요?", "MCM 서울 수선 센터 · 수선 작업 중"),
    ("AS-2026-00871", "언제 다 돼요?", "예상 완료일 안내"),
    ("AS-2026-00871", "어디까지 진행됐어요?", "완료 4단계 + 예정 3단계 (7단계 체계)"),
    ("AS-2026-00602", "제 지갑 받으려면 얼마나 남았어요?", "반환 배송 중 · 운송장 안내"),
    ("AS-2026-01033", "픽업 언제 와요?", "픽업 예약 완료 · 수거 대기 (타임라인 전부 예정)"),
]


def _quota_stop():
    """하루 한도에 걸렸으면 테스트를 중단한다. 같은 에러를 20번 볼 이유가 없다."""
    if not QUOTA_EXHAUSTED["hit"]:
        return False
    print("\n" + "!" * 90)
    print(f"  OpenAI 하루 요청 한도에 걸렸습니다. {QUOTA_EXHAUSTED['message']}")
    print("  · 무료 티어는 하루 50회 / 분당 10회입니다. --test 한 번에 24회를 씁니다.")
    print("  · 검색·안전장치만 확인하려면:  MCM_MOCK=1 python as_chatbot.py --test")
    print("  · 한도를 늘리려면 platform.openai.com → Billing에서 결제 수단 등록")
    print("!" * 90 + "\n")
    return True


def run_selftest():
    """검색·안전장치·답변 문구를 한 번에 확인한다. MCM_MOCK=1이면 GPT 없이 검색만 본다."""
    issues = 0
    if not MOCK:
        print(f"\n[안내] 실제 GPT를 {len(TEST_CASES) + len(PERSONAL_CASES) + 7}회 호출합니다. "
              "무료 티어는 하루 50회입니다.")

    for i, (q, expect) in enumerate(TEST_CASES, 1):
        r = answer(q)
        warn = _flag(q, expect, r)
        issues += len(warn)
        print("=" * 90)
        print(f"[{i:2}] {q}")
        print(f"     기대: {expect}")
        print(f"     GPT호출: {r['used_gpt']} / 개인정보가림: {r['masked']}건")
        print(f"     근거: {', '.join(r['sources']) or '없음'}")
        print(f"     답변: {r['answer']}")
        for w in warn:
            print(f"     🔴 {w}")
        if _quota_stop():
            return

    print("\n" + "=" * 90)
    print("개인화 테스트 — 접수 건을 알아야만 답할 수 있는 질문")
    for as_id, q, expect in PERSONAL_CASES:
        r = answer(q, None, as_id)
        print("=" * 90)
        print(f"  [{as_id}] 고객: {q}")
        print(f"     기대: {expect}")
        print(f"     근거: {', '.join(r['sources']) or '없음'}")
        print(f"     답변: {r['answer']}")
        if not any("내 AS 접수" in s for s in r["sources"]):
            issues += 1
            print("     🔴 접수 건 자료가 안 실렸습니다")
        if _quota_stop():
            return

    print("\n" + "=" * 90)
    print("연속 대화 테스트 — 앞 대화를 기억해야 통과")
    for turns in MULTITURN_CASES:
        print("=" * 90)
        history = []
        for q in turns:
            r = answer(q, history)
            mark = " (이전 대화 참고함)" if r.get("used_history") else ""
            print(f"  고객: {q}")
            print(f"  챗봇: {r['answer']}{mark}")
            if not r["sources"]:
                issues += 1
                print("     🔴 근거 없음 → 맥락을 놓쳤습니다")
            history += [{"role": "user", "content": q},
                        {"role": "assistant", "content": r["answer"]}]
        if _quota_stop():
            return
    print("=" * 90)
    print(f"자동 검출 경고 {issues}건. 나머지는 '답변'을 직접 읽고 판단하세요.\n")


if __name__ == "__main__":
    print(f"지식베이스 {len(KNOWLEDGE)}건 (AS 안내 {len(AS_KNOWLEDGE)} + 상품 {len(KNOWLEDGE)-len(AS_KNOWLEDGE)})")
    if MOCK:
        print("[MOCK 모드] GPT를 호출하지 않습니다.")

    if len(sys.argv) > 1 and sys.argv[1] in ("--test", "--selftest"):
        run_selftest()
    elif len(sys.argv) > 1:
        q = " ".join(sys.argv[1:])
        r = answer(q)
        print(f"\n질문: {q}\n답변: {r['answer']}\n근거: {r['sources']}")
    else:
        print("종료하려면 Ctrl+C / 대화 초기화는 '초기화' 입력\n")
        history = []            # 터미널에서도 대화를 기억한다
        while True:
            try:
                q = input("고객: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n종료")
                break
            if not q:
                continue
            if q == "초기화":
                history.clear()
                print("(대화를 초기화했습니다)\n")
                continue
            r = answer(q, history)
            print(f"챗봇: {r['answer']}")
            print(f"      (근거: {', '.join(r['sources']) or '없음'}"
                  f"{' / 이전 대화 참고' if r.get('used_history') else ''})\n")
            history += [{"role": "user", "content": q},
                        {"role": "assistant", "content": r["answer"]}]
            del history[:-HISTORY_TURNS * 2]
