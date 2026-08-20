"""FastAPI 요청/응답 스키마. 백엔드팀이 이 파일만 보고 바로 연동 가능하게 명시적으로 씀.

※ 타입힌트에 `X | None` (PEP 604) 대신 `Optional[X]`를 씀 - Python 3.9(예: macOS
   Command Line Tools 기본 python3)에서는 `X | None`이 pydantic이 타입을 eval하는
   시점에 TypeError를 냄 (3.10부터 지원되는 문법이라). Optional[X]는 3.7+ 어디서든 동작함."""

from typing import Optional

from pydantic import BaseModel, Field


class DamageItem(BaseModel):
    mcm_category: str = Field(..., description="손상유형 (예: 찢김/파열, 균열/파손, 변형, 손상(세부미상))")
    detection_confidence: float = Field(..., description="YOLO 탐지 confidence (0~1)")
    severity: str = Field(..., description="경미 | 보통 | 심각 (규칙 기반 휴리스틱, 학습된 값 아님)")
    area_ratio: float = Field(..., description="손상 bbox 면적 / 가방 bbox 면적 (0~1)")
    bag_box_detected: bool = Field(..., description="False면 가방 전체 박스를 못 찾아 이미지 전체 크기로 대체 계산함 (신뢰도 낮음)")
    estimated_cost_eur: dict = Field(..., description="{min, max, point_estimate} - 카테고리+심각도 기반 예상 수리비(유로)")
    cost_confidence_tag: str = Field(..., description="확인됨 | 부분확인 | 가설 - cost_mapping.py의 실측 근거 수준")


class DiagnoseResponse(BaseModel):
    filename: str
    image_width: int
    image_height: int
    damages: list[DamageItem]
    overall_severity: Optional[str] = Field(None, description="탐지된 손상 중 가장 심각한 등급. 손상 없으면 null")
    total_estimated_cost_eur: Optional[dict] = Field(None, description="손상별 point_estimate 합산 {min,max,point_estimate}. 손상 없으면 null")
    model_version: str
    warnings: list[str] = Field(default_factory=list, description="예: 가방 전체 박스 미탐지, confidence 낮음 등 주의사항")


class SingleImageDiagnosis(BaseModel):
    """/diagnose/multi 응답 안에서, 첨부된 사진 한 장 한 장의 개별(병합 전) 진단 결과."""
    filename: str
    image_width: int
    image_height: int
    damages: list[DamageItem]
    overall_severity: Optional[str] = None
    total_estimated_cost_eur: Optional[dict] = None
    warnings: list[str] = Field(default_factory=list)


class MultiDamageItem(DamageItem):
    source_image: str = Field(
        ..., description="같은 카테고리로 첨부된 여러 사진 중, 대표값(최댓값)으로 채택된 사진의 파일명"
    )


class MultiDiagnoseResponse(BaseModel):
    """가방 1개를 여러 장(2~3장, 각도/부위 다른 사진)으로 첨부했을 때의 병합 진단 응답.

    ⚠️ 병합 규칙(중요, 반드시 읽을 것): 여러 사진에 같은 mcm_category(손상유형)가 여러 번
    나오면, 그게 "같은 손상을 다른 각도에서 다시 찍은 것"인지 "가방의 다른 위치에 있는 별개
    손상"인지 자동으로 구분하지 않음 (이미지 재식별 모델이 필요한 영역이라 이번 스코프엔 없음).
    대신 카테고리별로 가장 심각한 것 하나만 대표로 채택해서 비용을 합산함 - 실제로 같은 유형의
    손상이 여러 군데 있으면 과소산정될 수 있음 (중복 합산으로 인한 과다청구보다 안전한 방향으로
    설계함). 원본 사진별 결과는 per_image_detail에 그대로 남겨뒀으니 필요하면 참고할 것."""
    n_images: int
    filenames: list[str]
    damages: list[MultiDamageItem]
    overall_severity: Optional[str] = Field(None, description="병합된 damages 중 가장 심각한 등급")
    total_estimated_cost_eur: Optional[dict] = Field(None, description="병합된(카테고리별 최댓값) damages의 point_estimate 합산")
    warnings: list[str] = Field(default_factory=list)
    per_image_detail: list[SingleImageDiagnosis] = Field(..., description="병합 전, 사진별 원본 진단 결과 (검수/디버깅용)")
    merge_method: str = Field(..., description="병합 규칙 설명 (사람이 읽는 용도)")
    model_version: str


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    model_path: Optional[str]
    classes: list[str]
    message: str
