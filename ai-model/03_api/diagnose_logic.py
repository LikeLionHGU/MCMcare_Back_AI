"""
이미지 1장 진단 조립 로직 + 여러 장(제품 1개, 각도/부위 다른 사진 2~3장) 결과 병합 로직.

main.py(FastAPI - /diagnose, /diagnose/multi)와 05_사용자스크립트/diagnose.py(개인용 CLI)가
둘 다 이 모듈을 그대로 가져다 씀. 조립/병합 규칙을 여러 곳에 복붙해두면 나중에 한쪽만 고치고
다른 쪽을 깜빡해서 서로 어긋나는 문제가 생기므로, 로직은 여기 한 곳에만 둠.
"""

from severity import compute_severity
from cost_mapping import estimate_cost

SEVERITY_RANK = {"경미": 0, "보통": 1, "심각": 2}


def assemble_diagnosis(detections, filename: str, img_w: int, img_h: int) -> dict:
    """YOLO 탐지 결과(detections) 1장 분량 -> 손상유형별 심각도/비용까지 조립된 dict 1개.
    main.py의 기존 /diagnose 응답 조립과 완전히 동일한 절차임."""
    severity_results = compute_severity(detections)

    damages = []
    warnings = []
    for r in severity_results:
        cost = estimate_cost(r["mcm_category"], r["area_ratio"])
        damages.append({
            "mcm_category": r["mcm_category"],
            "detection_confidence": r["detection_confidence"],
            "severity": r["severity"],
            "area_ratio": r["area_ratio"],
            "bag_box_detected": r["bag_box_detected"],
            "estimated_cost_eur": cost["estimated_cost_eur"],
            "cost_confidence_tag": cost["confidence_tag"],
        })
        if not r["bag_box_detected"]:
            warnings.append(
                f"'{r['mcm_category']}' 항목: 가방 전체 박스를 못 찾아 이미지 전체 크기 기준으로 "
                "심각도를 계산함 (신뢰도 낮음)"
            )

    overall_severity = None
    total_cost = None
    if damages:
        overall_severity = max(damages, key=lambda d: SEVERITY_RANK[d["severity"]])["severity"]
        total_cost = {
            "min": round(sum(d["estimated_cost_eur"]["min"] for d in damages), 2),
            "max": round(sum(d["estimated_cost_eur"]["max"] for d in damages), 2),
            "point_estimate": round(sum(d["estimated_cost_eur"]["point_estimate"] for d in damages), 2),
        }
    else:
        warnings.append("탐지된 손상이 없음 (가방이 정상이거나, confidence threshold 미만이거나, 모델 성능 한계일 수 있음)")

    return {
        "filename": filename,
        "image_width": img_w,
        "image_height": img_h,
        "damages": damages,
        "overall_severity": overall_severity,
        "total_estimated_cost_eur": total_cost,
        "warnings": warnings,
    }


def merge_multi_image(per_image_results: list) -> dict:
    """
    같은 제품(가방) 1개를 여러 장(2~3장) 찍은 사진들의 개별 진단 결과(assemble_diagnosis 출력들)를
    하나로 병합함.

    ⚠️ 중요 (설계상의 가설/규칙임, 검증된 알고리즘 아님):
      사진마다 프레임·각도·거리가 달라서, bbox 좌표만으로는 "사진 A의 손상"과 "사진 B의 손상"이
      실제로 같은 물리적 손상을 다시 찍은 건지 아니면 가방의 다른 위치에 있는 별개 손상인지
      구분할 방법이 없음 (이미지 임베딩 기반 재식별 모델이 있어야 풀리는 문제고, 이번 스코프엔
      없음 - 없는 걸 있는 척 안 함).

      그래서 안전한 쪽으로 규칙을 정함: **mcm_category(손상유형)별로, 여러 사진에 걸쳐 나온
      탐지들 중 가장 심각한 것(= severity 등급 우선, 동률이면 손상면적비가 큰 것) 딱 하나만
      대표값으로 채택**하고 나머지는 버림. "탐지 개수"가 아니라 "손상 유형 종류" 기준으로만
      비용을 합산함 - 같은 손상을 여러 장 찍었을 때 비용이 중복 합산되는 걸 막기 위함.

      한계 (반드시 알아야 함): 만약 실제로 같은 유형의 손상이 가방에 진짜로 2곳 이상 있다면
      (예: 균열이 서로 다른 위치에 2개), 이 로직은 그걸 1개로 과소산정함. 반대 방향(같은 손상을
      중복 합산해서 과다청구)보다는 안전한 실수라고 판단해서 이렇게 설계함 - 사람이 최종 검수
      하거나, 실제 운영 데이터가 쌓이면 재검토 필요함. (per_image_detail을 응답에 그대로 남겨서
      원본 사진별 결과도 항상 확인 가능하게 해둠.)
    """
    category_best: dict = {}
    for img_result in per_image_results:
        for d in img_result["damages"]:
            cat = d["mcm_category"]
            rank = (SEVERITY_RANK[d["severity"]], d["area_ratio"])
            if cat not in category_best or rank > category_best[cat][0]:
                category_best[cat] = (rank, d, img_result["filename"])

    merged_damages = []
    warnings = []
    for cat in sorted(category_best):
        _, d, src_filename = category_best[cat]
        merged_damages.append({**d, "source_image": src_filename})
        if not d["bag_box_detected"]:
            warnings.append(
                f"'{cat}' 항목(대표 출처: {src_filename}): 가방 전체 박스를 못 찾아 이미지 전체 "
                "크기 기준으로 계산함 (신뢰도 낮음)"
            )

    overall_severity = None
    total_cost = None
    if merged_damages:
        overall_severity = max(merged_damages, key=lambda d: SEVERITY_RANK[d["severity"]])["severity"]
        total_cost = {
            "min": round(sum(d["estimated_cost_eur"]["min"] for d in merged_damages), 2),
            "max": round(sum(d["estimated_cost_eur"]["max"] for d in merged_damages), 2),
            "point_estimate": round(sum(d["estimated_cost_eur"]["point_estimate"] for d in merged_damages), 2),
        }
    else:
        warnings.append("첨부된 모든 사진에서 탐지된 손상이 없음")

    return {
        "n_images": len(per_image_results),
        "filenames": [r["filename"] for r in per_image_results],
        "damages": merged_damages,
        "overall_severity": overall_severity,
        "total_estimated_cost_eur": total_cost,
        "warnings": warnings,
        "per_image_detail": per_image_results,
        "merge_method": "카테고리별 최댓값(severity 등급 우선, 동률이면 area_ratio 큰 쪽) 채택 - 상세 근거는 이 함수 docstring 참고",
    }
