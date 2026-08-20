"""
심각도(severity) 추정 모듈 - 규칙 기반(rule-based) 휴리스틱.

⚠️ 중요 (가설/제안 방법론임, 확인된 사실 아님):
  04_비전AI_학습데이터의 두 실제 공개 데이터셋(Roboflow bag-damage, Mendeley 9k3bf6ksnd)
  어디에도 "심각도" 라벨은 없음 (둘 다 손상 유형/위치만 라벨링돼있고 경중 등급이 없음).
  그래서 학습된(learned) 심각도 분류기가 아니라, 탐지된 손상 bbox 면적을
  가방 전체 bbox 면적으로 나눈 비율을 심각도의 대리 지표(proxy)로 쓰는 규칙 기반
  휴리스틱임. MCM의 실제 AS 판정 기준이 공개되면 이 로직을 학습 기반으로 교체하는 게
  맞음 (README의 "향후 개선 방향" 참고).

로직:
  1) YOLO 추론 결과에서 '가방전체(손상아님)' 박스를 찾음 (여러 개면 가장 confidence 높은 것)
  2) 가방 박스를 못 찾으면 이미지 전체 크기를 가방 크기로 대체함 (신뢰도 낮음 -> 플래그)
  3) 각 손상 박스 면적 / 가방 박스 면적 = area_ratio
  4) area_ratio를 0~1로 클리핑하고, 사전 정의된 3단계 임계값(가설)으로 라벨을 붙임:
       ratio <  0.05   -> "경미"
       0.05 <= ratio < 0.20 -> "보통"
       ratio >= 0.20   -> "심각"
     (임계값 근거: 실측 데이터 없음 - 상식적 가정. 실제 서비스 운영 데이터가 쌓이면
      재보정 필요함 - 확인 안 됨/가설)
"""

from dataclasses import dataclass

SEVERITY_THRESHOLDS = [
    (0.05, "경미"),
    (0.20, "보통"),
]
SEVERITY_MAX_LABEL = "심각"


@dataclass
class Detection:
    class_name: str
    confidence: float
    # 정규화 좌표 (0~1), YOLO 출력 그대로
    x_center: float
    y_center: float
    width: float
    height: float

    def area(self) -> float:
        return self.width * self.height

    def to_xyxy(self):
        x1 = self.x_center - self.width / 2
        y1 = self.y_center - self.height / 2
        x2 = self.x_center + self.width / 2
        y2 = self.y_center + self.height / 2
        return x1, y1, x2, y2


def _severity_label(ratio: float) -> str:
    for threshold, label in SEVERITY_THRESHOLDS:
        if ratio < threshold:
            return label
    return SEVERITY_MAX_LABEL


def compute_severity(detections: list[Detection], bag_class_name: str = "가방전체(손상아님)") -> list[dict]:
    """
    detections: 한 이미지에 대한 YOLO 탐지 결과 전체 (가방 박스 포함 가능)
    반환: 손상 박스별 심각도 정보 리스트 (가방 박스 자체는 결과에서 제외됨)
    """
    bag_boxes = [d for d in detections if d.class_name == bag_class_name]
    damage_boxes = [d for d in detections if d.class_name != bag_class_name]

    if bag_boxes:
        bag_box = max(bag_boxes, key=lambda d: d.confidence)
        bag_area = bag_box.area()
        bag_detected = True
    else:
        # 가방 전체 박스를 못 찾음 -> 이미지 전체(1.0 x 1.0 정규화 좌표)를 대체 기준으로 사용.
        # 이 경우 area_ratio 신뢰도가 떨어지므로 결과에 bag_detected=False로 명시함.
        bag_area = 1.0
        bag_detected = False

    results = []
    for d in damage_boxes:
        ratio = d.area() / bag_area if bag_area > 0 else 0.0
        ratio = max(0.0, min(1.0, ratio))
        results.append({
            "mcm_category": d.class_name,
            "detection_confidence": round(d.confidence, 4),
            "area_ratio": round(ratio, 4),
            "severity": _severity_label(ratio),
            "bag_box_detected": bag_detected,
        })

    return results
