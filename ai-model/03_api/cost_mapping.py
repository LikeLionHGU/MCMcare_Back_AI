"""
손상유형 + 심각도 -> 예상 수리비용(EUR) 매핑 모듈.

실측 데이터 근거 (확인됨):
  02_원본데이터/mcm_1star_reviews_raw.csv 의 실제 리뷰 텍스트에서 정규식으로 찾은
  MCM 공식 수리 견적 언급 2건 (Trustpilot 1점 리뷰, 둘 다 원문 인용 가능):

  1) review id_index=11 (독일어, 2023년 "Dessau" 가방):
     "Nun hat MCM geantwortet, sie würden die Tasche für 200 € reparieren."
     -> 가죽 전체 마모/변형(Leder... total abgenutzt) 수리 견적 = 200 EUR
     -> 본 코드에서는 '변형' 카테고리의 실측 앵커로 사용함

  2) review id_index=15 (독일어, 지갑 버클 교체):
     "Man hat mir den Austausch für eine Schnalle mit 120€ angeboten."
     -> 버클(하드웨어 부속) 교체 견적 = 120 EUR
     -> 본 코드에서는 '균열/파손' 카테고리(부속/구조 파손)의 실측 앵커로 사용함

  ⚠️ 이 2건 외 나머지 카테고리(찢김/파열, 손상(세부미상), 파손(바퀴))와
  각 카테고리의 정확한 심각도별 구간은 실측값이 없어서 위 2개 앵커를 기준으로
  상대적 난이도를 추론한 **가설(제안)** 값임. MCM 공식 AS 가격표가 공개되면
  COST_TABLE만 교체하면 됨 (코드 로직은 그대로 재사용 가능).

사용법:
  from cost_mapping import estimate_cost
  estimate_cost("변형", severity_ratio=0.15)  # -> {"min":..., "max":..., "point_estimate":..., ...}
"""

from dataclasses import dataclass


@dataclass
class CostRange:
    min_eur: float
    max_eur: float
    source: str
    confidence_tag: str  # "확인됨" | "가설"


# 카테고리별 비용 구간. min/max는 심각도 비율(0~1)에 따라 선형 보간함.
# - '변형', '균열/파손'은 실측 앵커가 있어서 그 값을 구간의 중심/한쪽 끝으로 사용함.
# - 나머지는 두 실측 앵커(120€, 200€) 대비 상대적으로 추정한 가설 값임:
#     찢김/파열: 원단 재봉/부분 교체가 필요해 균열/파손보다 비쌀 것으로 가정 (가설)
#     손상(세부미상): 원본 라벨이 단일 클래스라 세부 유형을 모름 -> 전체 구간의 평균 폭으로 넓게 잡음 (가설)
#     파손(바퀴): 실측 라벨 9개뿐이라 애초에 05_모델링코드 학습 대상에서도 제외했음.
#                 부품 완전 교체로 가정해 가장 비싼 축으로 잡음 (가설)
COST_TABLE: dict[str, CostRange] = {
    "변형": CostRange(
        min_eur=120.0, max_eur=200.0,
        source="Trustpilot 리뷰 id=11, 실측 200€ 견적을 상한으로 사용",
        confidence_tag="부분확인",
    ),
    "균열/파손": CostRange(
        min_eur=80.0, max_eur=150.0,
        source="Trustpilot 리뷰 id=15, 실측 120€ 견적을 구간 내부에 포함",
        confidence_tag="부분확인",
    ),
    "찢김/파열": CostRange(
        min_eur=100.0, max_eur=220.0,
        source="실측 앵커 없음 - 재봉/원단 교체가 더 큰 작업이라 가정",
        confidence_tag="가설",
    ),
    "손상(세부미상)": CostRange(
        min_eur=100.0, max_eur=200.0,
        source="원본(mendeley_9k3bf6ksnd) 라벨이 단일클래스라 세부유형 불명 - 위 카테고리들의 평균 구간 사용",
        confidence_tag="가설",
    ),
    "파손(바퀴)": CostRange(
        min_eur=150.0, max_eur=250.0,
        source="실측 라벨 9개뿐(학습셋 제외 클래스) - 부품 완전교체로 가정",
        confidence_tag="가설",
    ),
}

# 가방전체(손상아님)는 손상 카테고리가 아니라 severity 계산용 참조 박스라 비용표에 없음.
NON_DAMAGE_CATEGORIES = {"가방전체(손상아님)"}


def estimate_cost(mcm_category: str, severity_ratio: float) -> dict:
    """
    mcm_category: classes_mcm.txt / KEEP_CLASSES 중 하나
    severity_ratio: severity.py의 compute_severity()가 반환하는 0~1 사이 비율
                     (손상bbox면적 / 가방bbox면적). 클수록 비싸게 선형 보간함.
    """
    if mcm_category in NON_DAMAGE_CATEGORIES:
        raise ValueError(f"'{mcm_category}'는 손상 카테고리가 아니라 비용 산정 대상이 아님")

    entry = COST_TABLE.get(mcm_category)
    if entry is None:
        raise ValueError(
            f"cost_mapping.COST_TABLE에 '{mcm_category}' 카테고리가 없음. "
            "label_map.csv의 mcm_category_제안과 철자가 일치하는지 확인할 것."
        )

    ratio = max(0.0, min(1.0, severity_ratio))
    point_estimate = entry.min_eur + (entry.max_eur - entry.min_eur) * ratio

    return {
        "mcm_category": mcm_category,
        "severity_ratio": round(ratio, 4),
        "estimated_cost_eur": {
            "min": entry.min_eur,
            "max": entry.max_eur,
            "point_estimate": round(point_estimate, 2),
        },
        "confidence_tag": entry.confidence_tag,
        "source": entry.source,
    }
