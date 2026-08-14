#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCM 한국 공식몰 상품 크롤러 (v2)
- 왜 필요한가: 상품 상세 페이지에 흩어져 있는 소재/치수/이미지/영상 정보를 손으로 옮기지 않고 CSV로 한 번에 모으기 위함.
- MCM 페이지는 서버 렌더링이라 requests + BeautifulSoup만으로 충분하다 (Playwright 불필요).

사용법:
    pip install requests beautifulsoup4 lxml brotli
    python mcm_crawler.py                        # 아래 URLS 리스트를 크롤링
    python mcm_crawler.py urls.txt               # 파일에서 URL 읽기 (한 줄에 하나)
    python mcm_crawler.py urls.txt --debug       # 원본 HTML을 debug_html/ 에 저장
    python mcm_crawler.py urls.txt --cloudscraper # 403이 계속될 때 (pip install cloudscraper)
    python mcm_crawler.py --help                 # 403 대응 가이드 출력

출력:
    mcm_products.csv   수집 결과
    failed_urls.txt    실패한 URL 목록
    crawler.log        실행 로그
"""

import csv
import json
import logging
import os
import random
import re
import sys
import time
from urllib.parse import quote, unquote

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────

URLS = [
    "https://kr.mcmworldwide.com/ko_KR/남성/가방/백팩/stark-사이드-스터드-비세토스-백팩/MMKEAVE12CO001.html",
]

OUTPUT_CSV = "mcm_products.csv"
FAILED_LOG = "failed_urls.txt"
DEBUG_DIR = "debug_html"

DELAY_MIN, DELAY_MAX = 2.0, 4.0   # 요청 사이 랜덤 딜레이(초) — 403 대응으로 늘림
TIMEOUT = 25                       # 응답 대기 최대 시간(초)
MAX_RETRIES = 3                    # URL당 재시도 횟수

BASE = "https://kr.mcmworldwide.com"
HOME_URL = f"{BASE}/ko_KR/home"    # 쿠키를 받기 위해 먼저 방문할 페이지

# brotli(br) 압축은 라이브러리가 있어야 풀 수 있다. 없는데 br을 요청하면 응답이 깨진다.
try:
    import brotli  # noqa: F401
    _BR = True
except ImportError:
    try:
        import brotlicffi  # noqa: F401
        _BR = True
    except ImportError:
        _BR = False

ACCEPT_ENCODING = "gzip, deflate, br" if _BR else "gzip, deflate"

# 브라우저가 실제로 보내는 헤더 세트.
# requests는 기본 User-Agent가 'python-requests/2.x'라서 이것만 보고도 봇으로 차단당한다.
# Sec-Fetch-* 는 최신 크롬이 "이 요청이 어디서 왜 발생했는지"를 알려주는 헤더로,
# 이게 없으면 자동화 도구로 간주하는 WAF(웹 방화벽)가 많다.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": ACCEPT_ENCODING,
    "Cache-Control": "max-age=0",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",       # 요청마다 아래 build_headers에서 덮어씀
    "Sec-Fetch-User": "?1",
}


def ascii_url(url):
    """
    HTTP 헤더에는 아스키(영문·숫자·기호)만 넣을 수 있다.
    한글이 들어간 주소를 그대로 넣으면 'latin-1 codec' 오류가 난다.
    → 한글을 %EB%82%A8 같은 형태로 바꿔서 넣는다. (퍼센트 인코딩)
    """
    if not url:
        return url
    try:
        url.encode("latin-1")
        return url                      # 이미 아스키면 그대로
    except UnicodeEncodeError:
        # 스킴(https://)과 구분자는 남기고 나머지만 인코딩
        return quote(url, safe=":/?#[]@!$&'()*+,;=%~-._")


def build_headers(referer=None):
    """
    referer가 있으면 '같은 사이트 안에서 링크를 눌러 이동한 것'처럼 보이게 헤더를 구성한다.
    (주소창에 직접 입력 = Sec-Fetch-Site: none / 사이트 내 링크 클릭 = same-origin)
    """
    h = dict(BROWSER_HEADERS)
    if referer:
        h["Referer"] = ascii_url(referer)
        h["Sec-Fetch-Site"] = "same-origin"
    else:
        h.pop("Referer", None)
        h["Sec-Fetch-Site"] = "none"
    return h

FIELDS = [
    "product_code", "product_name", "price_krw", "color", "status",
    "material_body", "material_trim", "material_lining", "hardware",
    "dimensions", "strap_length", "handle_drop", "origin_country",
    "design_features", "color_variants", "image_urls", "video_urls", "product_url",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("crawler.log", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("mcm")


# ─────────────────────────────────────────────────────────────
# 1) 페이지 가져오기
# ─────────────────────────────────────────────────────────────

def is_local_file(path):
    """URL이 아니라 내 컴퓨터에 저장된 HTML 파일인지 판별."""
    return not path.lower().startswith("http") and path.lower().endswith((".html", ".htm"))


def read_local(path):
    """저장된 HTML 파일 읽기. 인코딩이 다를 수 있어 몇 가지를 순서대로 시도한다."""
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def make_session(use_cloudscraper=False):
    """
    Session을 만든다. Session을 쓰면 쿠키가 자동으로 유지되고 TCP 연결도 재사용된다.
    --cloudscraper 옵션을 주면 cloudscraper로 교체한다(설치되어 있을 때만).
    """
    if use_cloudscraper:
        try:
            import cloudscraper
            log.info("cloudscraper 모드로 실행")
            return cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "darwin", "mobile": False})
        except ImportError:
            log.warning("cloudscraper 미설치 → 일반 requests로 진행 (pip install cloudscraper)")
    return requests.Session()


def warmup(session):
    """
    상품 페이지를 바로 때리지 않고 홈을 먼저 방문한다.
    → 서버가 내려주는 세션 쿠키(장바구니/지역/WAF 통과용)를 Session에 저장하기 위함.
      사람도 보통 홈 → 상품 순으로 이동하므로 트래픽 패턴도 자연스러워진다.
    """
    try:
        res = session.get(HOME_URL, headers=build_headers(), timeout=TIMEOUT)
        log.info("홈 방문: %s (쿠키 %d개 확보)", res.status_code, len(session.cookies))
        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
        return res.status_code == 200
    except Exception as e:
        log.warning("홈 방문 실패: %s", e)
        return False


def fetch(session, url, referer=HOME_URL):
    """URL의 HTML을 문자열로 반환. 실패하면 예외를 던진다."""
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            res = session.get(ascii_url(url), headers=build_headers(referer), timeout=TIMEOUT)

            # 403이면 쿠키가 만료됐을 수 있으니 홈을 다시 방문해 세션을 되살린 뒤 재시도
            if res.status_code == 403 and attempt < MAX_RETRIES:
                log.warning("403 (%d/%d) → 홈 재방문 후 재시도", attempt, MAX_RETRIES)
                session.cookies.clear()
                warmup(session)
                time.sleep(3 * attempt)
                continue

            res.raise_for_status()          # 4xx/5xx면 예외
            if not res.encoding or res.encoding.lower() == "iso-8859-1":
                res.encoding = "utf-8"      # 한글 깨짐 방지
            return res.text
        except Exception as e:
            last_err = e
            log.warning("요청 실패 (%d/%d) %s → %s", attempt, MAX_RETRIES, url, e)
            time.sleep(3 * attempt)         # 백오프: 재시도할수록 더 오래 쉰다
    raise last_err


# ─────────────────────────────────────────────────────────────
# 2) 보조 함수
# ─────────────────────────────────────────────────────────────

def clean(text):
    """공백/개행 정리."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def dedupe(seq):
    """순서를 유지하면서 중복 제거. (MCM 불릿에는 같은 줄이 두 번 나오는 경우가 있다)"""
    seen, out = set(), []
    for x in seq:
        key = re.sub(r"\s+", "", x)      # 공백 차이는 무시하고 비교
        if key and key not in seen:
            seen.add(key)
            out.append(x)
    return out


def code_from_url(url):
    """URL 마지막 조각에서 상품코드 추출. 예: .../MMKEAVE12CO001.html → MMKEAVE12CO001"""
    m = re.search(r"/([A-Z0-9]{10,20})\.html", unquote(url), re.I)
    return m.group(1).upper() if m else ""


def to_int_price(text):
    """'₩1,890,000' → 1890000"""
    if text is None:
        return ""
    digits = re.sub(r"[^\d]", "", str(text).split(".")[0])
    return int(digits) if digits else ""


def iter_jsonld(soup):
    """
    JSON-LD: <script type="application/ld+json"> 안에 숨어 있는 구조화 상품 데이터.
    HTML 태그를 뒤지는 것보다 훨씬 안 깨지므로 1순위로 쓴다.
    """
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw.strip())
        except Exception:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                yield node
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))


def find_product_jsonld(soup):
    for node in iter_jsonld(soup):
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(str(x).lower() == "product" for x in types):
            return node
    return None


# ─────────────────────────────────────────────────────────────
# 3) 상세정보 불릿 분류
# ─────────────────────────────────────────────────────────────
# 실제 MCM 불릿 예시:
#   조절 가능한 어깨 스트랩        ← 디자인 특징 (길이 숫자 없음)
#   가죽 손잡이 부분              ← 디자인 특징
#   24K 도금 금속 장식            ← hardware
#   바디: 비세토스 모노그램 캔버스   ← material_body
#   트림: 천연 나파 가죽           ← material_trim
#   인조 나파 안감                ← material_lining
#   약 16 x 33 x 41 센티미터       ← dimensions
#   스트랩 길이: 76cm~90cm, 핸들 길이: 8cm  ← strap + handle 이 한 줄에 같이 있음!
#   제조국: 대한민국              ← origin_country

# 라벨이 붙은 줄은 라벨로 정확히 잡고, 값에서 라벨을 떼어낸다.
# 일부 상품은 상세정보가 번역되지 않고 영어로만 적혀 있다. 영어 라벨도 함께 본다.
LABELED = [
    ("material_body",   r"^(?:바디|본체|겉감|소재|Body|Shell)\s*[:：]\s*(.+)$"),
    ("material_trim",   r"^(?:트림|배색|Trim)\s*[:：]\s*(.+)$"),
    # 라벨 뒤 공백이 없는 경우도 있다 ("트림:천연 가죽")
    ("material_lining", r"^(?:안감|내부\s*소재|Lining)\s*[:：]\s*(.+)$"),
    ("origin_country",  r"^(?:제조국|원산지|생산지|Made\s*in|Country\s*of\s*origin)\s*[:：]?\s*(.+)$"),
]


def classify_bullets(bullets):
    """
    불릿 리스트를 필드별로 나눈다.
    어디에도 안 걸린 줄은 전부 design_features로 모은다.
    """
    out = {k: "" for k in ("material_body", "material_trim", "material_lining", "hardware",
                           "dimensions", "strap_length", "handle_drop")}
    out["origin_country"] = ""
    features = []

    def put(field, value):
        """같은 필드에 여러 줄이 걸리면 ' | '로 이어붙인다."""
        value = clean(value)
        if not value:
            return
        out[field] = (out[field] + " | " + value) if out[field] else value

    for line in bullets:
        line = clean(line)
        if not line or re.search(r"스타일\s*#", line):   # 스타일 번호 줄은 제외
            continue

        # (1) "라벨: 값" 형태 먼저
        hit = False
        for field, pat in LABELED:
            m = re.match(pat, line)
            if m:
                put(field, m.group(1))
                hit = True
                break
        if hit:
            continue

        # (2) 스트랩/핸들 길이 — 한 줄에 둘 다 들어있을 수 있으므로 각각 따로 뽑는다
        # 범위 구분자가 '~', '-', 'to' 등 페이지마다 다르다 ("76cm~90cm", "76 cm to 90 cm")
        # 영어 페이지는 값이 라벨 '앞'에 온다 ("104 cm to 128 cm strap length")
        RANGE = r"[\d.]+\s*(?:cm|센티미터)?\s*(?:[~\-–]|to)\s*[\d.]+\s*(?:cm|센티미터)?"
        s = (re.search(rf"(?:스트랩|어깨끈|숄더\s*끈|strap)\s*(?:길이|length)?\s*[:：]?\s*"
                       rf"({RANGE}|[\d.]+\s*(?:cm|센티미터))", line, re.I)
             or re.search(rf"({RANGE})\s*strap\s*length", line, re.I))
        h = re.search(r"(?:핸들|손잡이|드롭|handle|strap\s*drop)\s*(?:길이|드롭|drop)?\s*[:：]?\s*"
                      r"([\d.]+\s*(?:cm|센티미터))", line, re.I)
        if s or h:
            if s:
                put("strap_length", s.group(1))
            if h:
                put("handle_drop", h.group(1))
            continue

        # (3) 안감: 라벨 없이 접미어로 오는 경우
        #     "인조 나파 안감" / "Microfiber lining with suede finish"
        m = re.match(r"^(.+?)\s*안감$", line) or re.match(r"^(.+?)\s+lining\b(.*)$", line, re.I)
        if m:
            put("material_lining", " ".join(g for g in m.groups() if g).strip())
            continue

        # (4) 치수: "약 16 x 33 x 41 센티미터" → 숫자 부분만
        m = re.search(r"([\d.]+\s*[xX×]\s*[\d.]+(?:\s*[xX×]\s*[\d.]+)?)", line)
        if m and re.search(r"센티미터|cm|약", line, re.I):
            put("dimensions", re.sub(r"\s*[xX×]\s*", " x ", m.group(1)))
            continue

        # (5) 하드웨어: '장식'만으로는 부족(스터드 장식과 혼동). 도금/금속/24K 등이 있어야 한다.
        if re.search(r"\d+\s*K\s*도금|도금|금속\s*장식|하드웨어|brass|브라스", line, re.I):
            put("hardware", line)
            continue

        # (6) 나머지는 디자인 특징
        features.append(line)

    # ── 마무리 보정 ──────────────────────────────────────────
    # 가죽백·스카프 등 일부 페이지는 "바디:" 라벨 없이 소재만 한 줄로 적는다.
    #   예) "천연 나파 가죽", "100% 오가닉 실크"
    # material_body가 비어 있을 때만, 남은 특징 줄 중에서 '순수 소재 설명'을 찾아 올린다.
    # 단 "가죽 손잡이 부분"처럼 부품을 가리키는 줄은 제외해야 한다.
    if not out["material_body"]:
        MATERIAL_KW = r"가죽|캔버스|나일론|실크|코튼|울|캐시미어|카프스킨|스웨이드|폴리|레더|양가죽"
        PART_KW = (r"손잡이|핸들|스트랩|포켓|장식|클로저|슬리브|지퍼|루프|태슬|플레이트|"
                   r"수납|끈|고리|버클|참|밴드|디자인|프린트|모티프")
        for line in list(features):
            if (re.search(MATERIAL_KW, line)
                    and not re.search(PART_KW, line)
                    and len(line) <= 20):          # 소재 줄은 대개 짧다
                put("material_body", line)
                features.remove(line)              # 특징에서는 뺀다(중복 방지)
                break

    out["design_features"] = " | ".join(features)
    return out


def extract_detail_bullets(soup):
    """
    '제품 상세정보' 섹션의 <li> 텍스트를 순서 유지 + 중복 제거해서 반환.
    구조가 바뀌어도 버티도록 3단계로 시도한다.
    """
    bullets = []

    # (1) '스타일 #'이 들어있는 목록이 곧 제품 상세정보 목록이다 (가장 확실한 신호)
    for ul in soup.find_all(["ul", "ol"]):
        if re.search(r"스타일\s*#", ul.get_text(" ", strip=True)):
            bullets = [li.get_text(" ", strip=True) for li in ul.find_all("li")]
            break

    # (2) '제품 상세정보' 텍스트를 찾아 부모를 거슬러 올라가며 li 수집
    if not bullets:
        label = soup.find(string=re.compile(r"제품\s*상세\s*정보|Product\s*Details", re.I))
        node = label.parent if label else None
        for _ in range(6):
            if node is None:
                break
            lis = node.find_all("li")
            if len(lis) >= 3:
                bullets = [li.get_text(" ", strip=True) for li in lis]
                break
            node = node.parent

    # (3) 클래스명 기반 폴백
    if not bullets:
        block = soup.find(class_=re.compile(r"(product|pdp).*(detail|description)", re.I))
        if block:
            bullets = [li.get_text(" ", strip=True) for li in block.find_all("li")]

    return dedupe([b for b in (clean(x) for x in bullets) if b])


# ─────────────────────────────────────────────────────────────
# 4) 이미지 / 영상 / 색상 변형 추출
# ─────────────────────────────────────────────────────────────

IMG_RE = re.compile(r"https?://images\.mcmworldwide\.com/i/mcmworldwide/([A-Za-z0-9_\-]+)", re.I)
VID_RE = re.compile(r"https?://images\.mcmworldwide\.com/v/mcmworldwide/[A-Za-z0-9_\-]+/[A-Za-z0-9_\-]+", re.I)
# 색상 스와치 이미지에만 붙는 표식. 이걸로 "다른 색상 상품"을 정확히 골라낸다.
SWATCH_RE = re.compile(
    r"images\.mcmworldwide\.com/i/mcmworldwide/([A-Z0-9]{10,20})_\d+\?\$pdp-redesign-swatch\$", re.I)


def extract_media(html, code):
    """
    이미지 번호(01~06, 09, 10, 11 …)를 규칙으로 만들어내지 않고 HTML에서 직접 긁는다.
    img 태그 외에 srcset/JSON 안에 있는 것도 잡으려고 정규식으로 전체를 훑는다.
    """
    # --- 이미지: 현재 상품코드가 들어간 것만 ---
    images = []
    for m in IMG_RE.finditer(html):
        name = m.group(1)                       # 예: MMKEAVE12CO001_09
        if code and not name.upper().startswith(code.upper()):
            continue                            # 스와치/추천상품 이미지 제외
        images.append(f"https://images.mcmworldwide.com/i/mcmworldwide/{name}")

    # --- 영상 ---
    # 같은 영상이 webm_720p / mp4 / thumbs(썸네일 이미지) 등 여러 형태로 나온다.
    # 썸네일은 영상이 아니므로 버리고, 실제 재생 파일만 남긴다.
    # 영상 하나가 화질별로 6개(webm/mp4 × 720p/480p/240p)씩 나오고 썸네일도 섞인다.
    # 영상 1개당 URL 1개만 남긴다. 우선순위: webm_720p > mp4_720p > 나머지
    RANK = {"webm_720p": 0, "mp4_720p": 1, "webm_480p": 2, "mp4_480p": 3,
            "webm_240p": 4, "mp4_240p": 5}
    best = {}
    for v in VID_RE.findall(html):
        v = v.split("?")[0]
        vid_id, _, rendition = v.rpartition("/")
        if rendition in ("thumbs", "poster"):
            continue                       # 썸네일은 영상이 아니다
        rank = RANK.get(rendition, 99)
        if vid_id not in best or rank < best[vid_id][0]:
            best[vid_id] = (rank, v)
    videos = [v for _, v in best.values()]

    # --- 색상 변형: 스와치에 박힌 '다른 상품코드' ---
    variants = [c.upper() for c in SWATCH_RE.findall(html)]
    if not variants:
        # 스와치 표식이 없으면 전체 이미지에서 코드만 추출하는 방식으로 폴백
        variants = [n.split("_")[0].upper() for n in IMG_RE.findall(html)
                    if re.fullmatch(r"[A-Z0-9]{10,20}_\d+", n.upper())]
    variants = [c for c in variants if c != (code or "").upper()]

    return dedupe(images), dedupe(videos), dedupe(variants)


# ─────────────────────────────────────────────────────────────
# 5) 재고 상태 판정
# ─────────────────────────────────────────────────────────────

def detect_status(page_text):
    """
    ACTIVE / SOLD_OUT 판정.
    주의: '현재 이 상품은 품절입니다'는 사이즈 선택 모달 안에 항상 들어있어서
    그것만 보고 판단하면 판매 중인 상품도 품절로 잘못 찍힌다.
    → 판매 중 신호('N개 남음', '소량 재고', '쇼핑백에 추가')를 먼저 확인한다.
    """
    if re.search(r"\d+\s*개\s*남음", page_text):
        return "ACTIVE"
    if re.search(r"소량\s*재고|재고\s*있음|IN\s*STOCK", page_text, re.I):
        return "ACTIVE"
    if re.search(r"쇼핑백에\s*추가|장바구니에?\s*담기|ADD TO (BAG|CART)", page_text, re.I) \
            and not re.search(r"전\s*사이즈\s*품절|SOLD\s*OUT", page_text, re.I):
        return "ACTIVE"
    if re.search(r"품절|SOLD\s*OUT|재입고\s*알림", page_text, re.I):
        return "SOLD_OUT"
    return ""


# ─────────────────────────────────────────────────────────────
# 6) 페이지 1개 파싱
# ─────────────────────────────────────────────────────────────

def parse_product(html, url):
    """HTML 문자열 → CSV 한 줄(dict)"""
    soup = BeautifulSoup(html, "lxml")
    page_text = soup.get_text(" ", strip=True)
    row = {k: "" for k in FIELDS}
    row["product_url"] = url

    # 로컬 파일을 읽은 경우엔 파일 경로 대신 페이지에 박힌 정식 주소(canonical)를 쓴다
    if is_local_file(url):
        canon = soup.find("link", rel="canonical")
        if canon and canon.get("href"):
            row["product_url"] = canon["href"]

    # --- 상품코드: 본문 '스타일 #' 우선, 없으면 URL ---
    m = re.search(r"스타일\s*#\s*([A-Z0-9]+)", page_text, re.I)
    row["product_code"] = (m.group(1) if m else code_from_url(url)).upper()

    # --- JSON-LD 우선 ---
    ld = find_product_jsonld(soup)
    if ld:
        row["product_name"] = clean(ld.get("name"))
        row["color"] = clean(ld.get("color"))
        offers = ld.get("offers")
        if isinstance(offers, list) and offers:
            offers = offers[0]
        if isinstance(offers, dict):
            row["price_krw"] = to_int_price(offers.get("price"))

    # --- 상품명 폴백 ---
    if not row["product_name"]:
        h1 = soup.find("h1")
        og = soup.find("meta", property="og:title")
        row["product_name"] = clean(h1.get_text()) if h1 else clean(og["content"]) if og and og.get("content") else ""

    # --- 가격 폴백: '₩1,890,000' 또는 '1,890,000원' ---
    if not row["price_krw"]:
        m = re.search(r"₩\s*([\d,]{4,})", page_text) or re.search(r"([\d,]{4,})\s*원", page_text)
        if m:
            row["price_krw"] = to_int_price(m.group(1))

    # --- 색상 폴백 ---
    # 1순위: 현재 상품코드가 붙은 색상 스와치 이미지의 title 속성 (가장 정확)
    # 2순위: '색상: cognac' 텍스트. 단어 하나만 잘라낸다.
    #        (주변에 '현재 이 상품은 품절입니다' 같은 문구가 붙어 오는 걸 막기 위함)
    if not row["color"]:
        for img in soup.find_all("img", src=re.compile(r"pdp-redesign-swatch", re.I)):
            src = img.get("src", "")
            if row["product_code"] and row["product_code"].upper() in src.upper():
                row["color"] = clean(img.get("title") or img.get("alt")).lower()
                break
    if not row["color"]:
        m = re.search(r"색상\s*[:：]\s*([A-Za-z][A-Za-z\-]{1,19}|[가-힣]{2,10})", page_text)
        if m:
            row["color"] = clean(m.group(1)).lower()

    # --- 재고 상태 ---
    row["status"] = detect_status(page_text)

    # --- 상세정보 불릿 ---
    bullets = extract_detail_bullets(soup)
    row.update(classify_bullets(bullets))

    # --- 미디어 & 색상 변형 ---
    images, videos, variants = extract_media(html, row["product_code"])
    row["image_urls"] = " | ".join(images)
    row["video_urls"] = " | ".join(videos)
    row["color_variants"] = " | ".join(variants)

    return row, bullets


# ─────────────────────────────────────────────────────────────
# 7) 메인 루프
# ─────────────────────────────────────────────────────────────

def crawl(urls, debug=False, use_cloudscraper=False):
    session = make_session(use_cloudscraper)
    rows, failed = [], []
    forbidden = 0

    if debug:
        os.makedirs(DEBUG_DIR, exist_ok=True)

    warmup(session)                 # ① 홈 먼저 방문해서 쿠키 확보
    referer = HOME_URL              # 첫 상품은 홈에서 넘어온 것처럼

    # CSV를 미리 열어두고 한 건 성공할 때마다 바로 쓴다.
    # 중간에 Ctrl+C로 멈추거나 오류가 나도 그때까지 모은 건 남는다.
    csv_file = open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig")
    writer = csv.DictWriter(csv_file, fieldnames=FIELDS)
    writer.writeheader()
    csv_file.flush()

    for i, url in enumerate(urls, 1):
        url = url.strip()
        if not url or url.startswith("#"):
            continue
        log.info("[%d/%d] %s", i, len(urls), url)
        try:
            if is_local_file(url):
                # 브라우저에서 직접 저장한 HTML 파일(.html)을 읽는다.
                # 403으로 막혔을 때 쓰는 우회 아닌 우회: 내가 브라우저로 본 페이지를
                # 저장해서 파싱만 코드로 하는 것이라 서버에 추가 요청을 보내지 않는다.
                html = read_local(url)
                log.info("  ↳ 로컬 파일에서 읽음")
            else:
                html = fetch(session, url, referer=referer)
                referer = url       # 다음 상품은 직전 상품에서 넘어온 것처럼
            row, bullets = parse_product(html, url)

            if not row["product_name"]:
                raise ValueError("상품명 추출 실패 — 차단되었거나 페이지 구조가 다름")
            if len(bullets) < 3:
                log.warning("  ↳ 상세정보 불릿이 %d개뿐 (선택자 확인 필요)", len(bullets))

            rows.append(row)
            writer.writerow(row)        # 한 건 끝날 때마다 즉시 저장
            csv_file.flush()
            log.info("  ↳ OK %s / %s / %s원 / %s / 불릿%d 이미지%d 영상%d 색상변형%d",
                     row["product_code"], row["product_name"], row["price_krw"], row["status"],
                     len(bullets),
                     len(images_of(row)), len(videos_of(row)), len(variants_of(row)))

            if debug:
                with open(os.path.join(DEBUG_DIR, f"{row['product_code'] or i}.html"),
                          "w", encoding="utf-8") as f:
                    f.write(html)

        except Exception as e:
            log.error("  ↳ 실패: %s", e)
            if "403" in str(e):
                forbidden += 1
            failed.append(f"{url}\t{e}")
        finally:
            if i < len(urls):
                time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    csv_file.close()

    if failed:
        with open(FAILED_LOG, "w", encoding="utf-8") as f:
            f.write("\n".join(failed))

    log.info("완료: 성공 %d건 / 실패 %d건 → %s", len(rows), len(failed), OUTPUT_CSV)

    if forbidden and not use_cloudscraper:
        log.error(
            "403이 %d건입니다. 헤더 보강으로도 막혔다면 다음 순서로 시도하세요:\n"
            "  1) pip install cloudscraper  →  python mcm_crawler.py urls.txt --cloudscraper\n"
            "  2) 그래도 막히면 Playwright (아래 PLAYWRIGHT_HINT 참고)", forbidden)
    return rows, failed


# ─────────────────────────────────────────────────────────────
# 403이 계속될 때의 대안
# ─────────────────────────────────────────────────────────────
PLAYWRIGHT_HINT = r"""
[1단계] cloudscraper — 코드 거의 그대로, 가장 저렴한 선택
    pip install cloudscraper
    python mcm_crawler.py urls.txt --cloudscraper
  Cloudflare의 자바스크립트 검사를 대신 통과해주는 라이브러리.
  파싱 함수(parse_product 등)는 손댈 필요 없다.

[2단계] Playwright — 실제 크롬을 띄우므로 거의 확실히 뚫리지만 느리고 무겁다
    pip install playwright && playwright install chromium

  아래 함수를 이 파일에 추가하고, crawl() 안의 fetch(...) 호출을
  fetch_playwright(url) 로 바꾸면 나머지 파싱 로직은 그대로 재사용된다:

    from playwright.sync_api import sync_playwright

    def fetch_playwright(urls):
        htmls = {}
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(locale="ko-KR", user_agent=BROWSER_HEADERS["User-Agent"])
            page = ctx.new_page()
            page.goto(HOME_URL, wait_until="domcontentloaded")   # 홈에서 쿠키 확보
            for u in urls:
                page.goto(u, wait_until="domcontentloaded")
                htmls[u] = page.content()
                time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
            browser.close()
        return htmls

[판단 기준]
  - 403 응답 본문에 'Cloudflare' / 'Attention Required' 문구가 보이면 → 1단계로 충분한 경우가 많다
  - 403 대신 캡차/자바스크립트 챌린지 페이지가 오면 → 2단계 필요
  - 어느 쪽이든 딜레이를 5~8초로 더 늘리고 한 번에 20~30건씩만 돌리는 게 안전하다
"""


# 로그 출력용 소소한 헬퍼
def images_of(row):   return [x for x in row["image_urls"].split(" | ") if x]
def videos_of(row):   return [x for x in row["video_urls"].split(" | ") if x]
def variants_of(row): return [x for x in row["color_variants"].split(" | ") if x]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        print(PLAYWRIGHT_HINT)
        return

    debug = "--debug" in sys.argv
    use_cs = "--cloudscraper" in sys.argv

    # 인자 해석 순서가 중요하다.
    # .html 파일을 넘겼는데 이걸 'URL 목록 파일'로 착각하면
    # HTML 본문 한 줄 한 줄을 URL로 읽어버린다. 그래서 HTML을 먼저 걸러낸다.
    if args and is_local_file(args[0]):
        urls = args                      # 저장한 HTML 파일 1개 이상
    elif args and os.path.exists(args[0]):
        with open(args[0], encoding="utf-8") as f:   # urls.txt 같은 목록 파일
            # 빈 줄과 '#' 주석은 여기서 미리 걸러낸다.
            # (아래 안전장치보다 먼저 처리해야 주석을 URL로 오해하지 않는다)
            urls = [ln.strip() for ln in f
                    if ln.strip() and not ln.strip().startswith("#")]
    elif args:
        urls = args                      # 주소를 직접 넘긴 경우
    else:
        urls = URLS

    # 안전장치: 목록에 이상한 게 섞이면(HTML 본문을 잘못 읽은 경우 등) 중단한다
    bad = [u for u in urls if not (u.lower().startswith("http") or is_local_file(u))]
    if bad:
        log.error("URL도 파일명도 아닌 값이 %d개 섞여 있습니다. 첫 항목: %.60s",
                  len(bad), bad[0])
        log.error("→ urls.txt에 HTML 본문이 들어갔거나, 파일 형식이 잘못됐을 수 있습니다.")
        return

    crawl(urls, debug=debug, use_cloudscraper=use_cs)


if __name__ == "__main__":
    main()
