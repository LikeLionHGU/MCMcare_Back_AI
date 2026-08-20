#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCM 카테고리 페이지에서 상품 URL을 자동으로 수집한다.

[왜 필요한가]
  mcm_crawler.py는 'URL 목록'을 받아서 상세 정보를 긁는다.
  그 목록(urls.txt)을 지금까지는 손으로 채웠기 때문에 24건에 머물렀다.
  카테고리 페이지를 훑어서 상품 URL을 자동으로 모으면 지식베이스를 크게 늘릴 수 있다.

[재사용]
  403 차단 우회(브라우저 헤더 + 홈 방문 후 쿠키 확보)는 mcm_crawler.py에 이미 있다.
  같은 코드를 두 번 쓰지 않도록 거기서 가져다 쓴다.

[실행]
  python collect_urls.py                    # 기본 카테고리 전부 수집 → urls.txt 갱신
  python collect_urls.py --out more.txt     # 다른 파일로 저장
  python collect_urls.py --limit 60         # 카테고리당 최대 60개만
  python collect_urls.py --dry              # 저장하지 않고 개수만 확인

[주의]
  상대 서버에 부담을 주지 않도록 요청 사이에 쉰다. 급하다고 지우지 말 것.
"""

import re
import sys
import time
import random

from bs4 import BeautifulSoup

# 크롤러의 세션·헤더·재시도 로직을 그대로 재사용한다
from mcm_crawler import make_session, warmup, fetch, ascii_url, log

BASE = "https://kr.mcmworldwide.com"

# 수집 대상 카테고리. 필요하면 여기에 줄만 추가하면 된다.
CATEGORIES = {
    "가방 전체":      "/ko_KR/%EA%B0%80%EB%B0%A9/%EB%AA%A8%EB%91%90%EB%B3%B4%EA%B8%B0",
    "백팩":           "/ko_KR/%EA%B0%80%EB%B0%A9/%EB%B0%B1%ED%8C%A9",
    "여성 핸드백":     "/ko_KR/%EC%97%AC%EC%84%B1/%ED%95%B8%EB%93%9C%EB%B0%B1/%EB%AA%A8%EB%91%90%EB%B3%B4%EA%B8%B0",
    "남성 가방":       "/ko_KR/%EB%82%A8%EC%84%B1/%EA%B0%80%EB%B0%A9/%EB%AA%A8%EB%91%90%EB%B3%B4%EA%B8%B0",
    "여성 지갑·소품":  "/ko_KR/%EC%97%AC%EC%84%B1/%EC%A7%80%EA%B0%91-%EB%A0%88%EB%8D%94%EC%86%8C%ED%92%88/%EB%AA%A8%EB%91%90%EB%B3%B4%EA%B8%B0",
    "남성 지갑·소품":  "/ko_KR/%EB%82%A8%EC%84%B1/%EC%A7%80%EA%B0%91-%EB%A0%88%EB%8D%94%EC%86%8C%ED%92%88/%EB%AA%A8%EB%91%90%EB%B3%B4%EA%B8%B0",
    "트래블":         "/ko_KR/%ED%8A%B8%EB%9E%98%EB%B8%94/%EB%AA%A8%EB%91%90%EB%B3%B4%EA%B8%B0",

    # --- 2026-08-13 추가: 가방·지갑 외 전 카테고리 ---
    "여성 패션소품":       "/ko_KR/%EC%97%AC%EC%84%B1/%ED%8C%A8%EC%85%98%EC%86%8C%ED%92%88/%EB%AA%A8%EB%91%90%EB%B3%B4%EA%B8%B0",
    "여성 의류":         "/ko_KR/%EC%97%AC%EC%84%B1/%EC%9D%98%EB%A5%98/%EB%AA%A8%EB%91%90%EB%B3%B4%EA%B8%B0",
    "여성 슈즈":         "/ko_KR/%EC%97%AC%EC%84%B1/%EC%8A%88%EC%A6%88/%EB%AA%A8%EB%91%90%EB%B3%B4%EA%B8%B0",
    "남성 패션소품":       "/ko_KR/%EB%82%A8%EC%84%B1/%ED%8C%A8%EC%85%98%EC%86%8C%ED%92%88/%EB%AA%A8%EB%91%90%EB%B3%B4%EA%B8%B0",
    "남성 의류":         "/ko_KR/%EB%82%A8%EC%84%B1/%EC%9D%98%EB%A5%98/%EB%AA%A8%EB%91%90%EB%B3%B4%EA%B8%B0",
    "남성 슈즈":         "/ko_KR/%EB%82%A8%EC%84%B1/%EC%8A%88%EC%A6%88/%EB%AA%A8%EB%91%90%EB%B3%B4%EA%B8%B0",
    "라이프스타일":        "/ko_KR/%EB%9D%BC%EC%9D%B4%ED%94%84%EC%8A%A4%ED%83%80%EC%9D%BC/%EB%AA%A8%EB%91%90%EB%B3%B4%EA%B8%B0",
}

# 상품 상세 URL 형태: /ko_KR/.../<상품코드>.html
#   짧은 형태  : /ko_KR/stark-디스코-비세토스-백팩/MMKGAVE02CO001.html
#   긴 형태    : /ko_KR/여성/패션소품/스카프/로레토스-자카드/MEFEAMM06Y9001.html
# 카테고리 깊이가 제각각이라 중간 경로 수를 고정하면 안 된다.
PRODUCT_RE = re.compile(r"^/ko_KR/(?:[^/]+/)+([A-Z0-9]{10,18})\.html$")

PAGE_SIZE = 200        # 한 번에 받아올 상품 수 (SFCC 계열은 sz 파라미터를 지원한다)
DELAY_MIN, DELAY_MAX = 1.5, 3.0


def product_links(html):
    """
    카테고리 HTML에서 상품 상세 URL만 골라낸다. 반환: [(상품코드, URL), ...]

    상품코드를 같이 돌려주는 이유:
    같은 상품이 여러 카테고리 경로로 접근 가능해서 URL만으로는 중복을 못 거른다.
      /ko_KR/스타크-사이드-스터드-비세토스-백팩/MMKEAVE14CO001.html
      /ko_KR/남성/가방/백팩/스타크-사이드-스터드-비세토스-백팩/MMKEAVE14CO001.html
    URL은 다르지만 같은 상품이다. 이걸 안 거르면 크롤링을 두 배로 하고
    챗봇 답변에도 같은 상품이 여러 번 나온다. (실제로 263건 중 138건이 중복이었다)
    """
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0].split("#")[0]
        if href.startswith(BASE):
            href = href[len(BASE):]
        m = PRODUCT_RE.match(href)
        if m:
            found.append((m.group(1), BASE + href))
    return found


def collect(session, name, path, seen_codes, limit=None):
    """
    카테고리 하나에서 상품 URL을 모은다. 페이지가 나뉘면 이어서 받는다.
    seen_codes: 이미 다른 카테고리에서 나온 상품코드 집합 (전역 중복 제거)
    """
    urls, start = [], 0
    while True:
        url = f"{BASE}{path}?start={start}&sz={PAGE_SIZE}"
        try:
            html = fetch(session, url, referer=BASE + path)
        except Exception as e:
            log.warning("[%s] 페이지 실패 start=%d → %s", name, start, e)
            break

        page = product_links(html)
        new = []
        for code, u in page:
            if code in seen_codes:
                continue
            seen_codes.add(code)
            new.append(u)
            if limit and len(urls) + len(new) >= limit:
                break

        urls.extend(new)
        log.info("[%s] start=%d → 새 상품 %d개 (누적 %d, 전체 %d)",
                 name, start, len(new), len(urls), len(seen_codes))

        # 새로 얻은 게 없으면 마지막 페이지로 본다
        if not page or (limit and len(urls) >= limit):
            break
        start += PAGE_SIZE
        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    return urls


def load_existing(path):
    """이미 있는 urls.txt를 읽는다. 주석과 빈 줄은 건너뛴다."""
    try:
        with open(path, encoding="utf-8") as f:
            return [ln.strip() for ln in f
                    if ln.strip() and not ln.strip().startswith("#")]
    except FileNotFoundError:
        return []


def main():
    out = "urls.txt"
    limit = None
    dry = "--dry" in sys.argv

    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    session = make_session()
    if not warmup(session):
        log.warning("홈 방문에 실패했습니다. 그래도 진행은 해봅니다.")

    before = load_existing(out)
    all_urls = list(before)

    # 기존 목록에 이미 있는 상품코드도 '본 것'으로 등록해 중복 수집을 막는다
    seen_codes = set()
    for u in before:
        m = PRODUCT_RE.match(u.replace(BASE, ""))
        if m:
            seen_codes.add(m.group(1))

    per_category = {}
    for name, path in CATEGORIES.items():
        got = collect(session, name, path, seen_codes, limit)
        per_category[name] = len(got)
        all_urls.extend(got)
        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    added = len(all_urls) - len(before)

    print("\n" + "=" * 60)
    for name, n in per_category.items():
        print(f"  {name:14s} {n:4d}개 (새 상품만)")
    print("=" * 60)
    print(f"  기존 {len(before)}개 → 전체 {len(all_urls)}개 (신규 {added}개)")
    print(f"  ※ 상품코드 기준 중복 제거됨. 같은 상품의 다른 카테고리 경로는 건너뜀")

    if dry:
        print("  --dry 모드라 저장하지 않았습니다.")
        return

    with open(out, "w", encoding="utf-8") as f:
        f.write("# MCM 상품 URL 목록 - 한 줄에 하나. #으로 시작하면 건너뜀\n")
        f.write(f"# collect_urls.py 자동 수집 ({time.strftime('%Y-%m-%d %H:%M')})\n")
        for u in all_urls:
            f.write(u + "\n")

    print(f"  → {out} 저장 완료")
    print(f"\n다음 단계:  python mcm_crawler.py {out}")


if __name__ == "__main__":
    main()
