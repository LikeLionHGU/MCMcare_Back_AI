"""
MCM 케어 - 가방 손상 진단 AI 예상견적 API

백엔드팀 연동 가이드:
  POST /diagnose       - multipart/form-data로 이미지 1장 업로드 -> 손상유형/심각도/예상비용 JSON
  POST /diagnose/multi - multipart/form-data로 같은 가방 1개를 찍은 사진 여러 장(2~3장 등)을
                          한 번에 업로드 -> 사진별 결과 + 카테고리별 최댓값으로 병합한 결과 JSON
                          (병합 규칙 한계 있음 - schemas.MultiDiagnoseResponse 및
                          diagnose_logic.py의 merge_multi_image() docstring 꼭 읽을 것)
  GET  /health          - 모델 로딩 상태 확인용 (배포 후 헬스체크에 연결하면 됨)
  GET  /                - 위 엔드포인트 안내

실행:
  pip install -r requirements.txt
  uvicorn main:app --host 0.0.0.0 --port 8000

  (Swagger 문서는 실행 후 http://localhost:8000/docs 에서 바로 확인 가능함)

⚠️ 현재 상태 (확인됨, README.md에 상세 설명):
  - 이 API 코드 자체는 완성/실행 가능한 상태임.
  - 다만 이 코드를 만든 샌드박스가 인터넷 차단 환경이라 실제 학습된 가중치(.pt)를
    이 세션에서 만들지 못했음. MODEL_PATH에 진짜 best.pt가 없으면 서버는 뜨지만
    /diagnose, /diagnose/multi가 503(모델 미탑재)을 반환함 - 가짜 결과를 돌려주지 않음.
  - 02_train_yolo.py를 인터넷 되는 환경에서 돌려서 best.pt를 만든 뒤
    02_weights/sanity_check/weights/best.pt 에 두면(또는 MODEL_PATH 환경변수로 경로 지정)
    바로 정상 동작함.
"""

import shutil
import tempfile
from pathlib import Path
from typing import List

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from diagnose_logic import assemble_diagnosis, merge_multi_image
from model_loader import CLASSES, MODEL_PATH, DamageDetector, ModelNotLoadedError
from schemas import DamageItem, DiagnoseResponse, HealthResponse, MultiDamageItem, MultiDiagnoseResponse, SingleImageDiagnosis

MODEL_VERSION = "yolov8-main_run-v1 (yolov8s·50ep·GPU, test mAP50=0.590, 04_비전AI_학습데이터 기반)"

app = FastAPI(
    title="MCM 케어 - 가방 손상 진단 API",
    description="이미지를 넣으면 손상유형/심각도/예상 수리비용(EUR)을 반환함. "
                 "심각도는 학습된 값이 아니라 bbox 면적비 기반 규칙(heuristic)임 - 상세는 README 참고.",
    version="0.1.0-sanity-check",
)

detector = DamageDetector()


@app.get("/")
def root():
    return {
        "service": "MCM 케어 - 가방 손상 진단 API",
        "endpoints": [
            "/diagnose (POST, multipart image 1장)",
            "/diagnose/multi (POST, multipart image 여러 장 - 가방 1개를 여러 각도로 촬영)",
            "/health (GET)",
            "/docs",
        ],
    }


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(
        status="ok" if detector.is_loaded else "model_not_loaded",
        model_loaded=detector.is_loaded,
        model_path=str(MODEL_PATH),
        classes=CLASSES,
        message="정상" if detector.is_loaded else (detector.load_error or "알 수 없는 오류"),
    )


def _predict_one(file: UploadFile) -> dict:
    """업로드 파일 1개 -> assemble_diagnosis() 결과 dict. 파일 검증 + 임시저장 + 추론 + 조립까지."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail=f"이미지 파일이 아님: {file.filename} ({file.content_type})")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / (file.filename or "upload.jpg")
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        try:
            detections, img_w, img_h = detector.predict(str(tmp_path))
        except ModelNotLoadedError as e:
            raise HTTPException(
                status_code=503,
                detail=f"모델이 아직 준비 안 됨: {e}. GET /health로 상세 상태 확인할 것.",
            ) from e

    return assemble_diagnosis(detections, file.filename or "unknown", img_w, img_h)


@app.post("/diagnose", response_model=DiagnoseResponse)
async def diagnose(file: UploadFile = File(...)):
    result = _predict_one(file)
    return DiagnoseResponse(
        filename=result["filename"],
        image_width=result["image_width"],
        image_height=result["image_height"],
        damages=[DamageItem(**d) for d in result["damages"]],
        overall_severity=result["overall_severity"],
        total_estimated_cost_eur=result["total_estimated_cost_eur"],
        model_version=MODEL_VERSION,
        warnings=result["warnings"],
    )


@app.post("/diagnose/multi", response_model=MultiDiagnoseResponse)
async def diagnose_multi(files: List[UploadFile] = File(...)):
    """같은 가방 1개를 여러 장(2~3장 등, 각도/부위 다르게) 찍은 사진들을 한 번에 진단함.

    병합 규칙(중요, 한계 있음): 같은 손상유형이 여러 사진에 걸쳐 나오면 "같은 손상 재촬영"인지
    "다른 위치의 별개 손상"인지 자동으로 구분 못 함 (이미지 재식별 모델 없음). 그래서 카테고리별
    최댓값 하나만 대표로 채택해서 합산함 - 상세는 diagnose_logic.merge_multi_image() docstring
    또는 응답의 merge_method 필드 참고. 원본 사진별 결과는 per_image_detail에 그대로 남아있음."""
    if not files:
        raise HTTPException(status_code=400, detail="이미지가 1장도 첨부되지 않음")

    per_image_results = [_predict_one(f) for f in files]
    merged = merge_multi_image(per_image_results)

    return MultiDiagnoseResponse(
        n_images=merged["n_images"],
        filenames=merged["filenames"],
        damages=[MultiDamageItem(**d) for d in merged["damages"]],
        overall_severity=merged["overall_severity"],
        total_estimated_cost_eur=merged["total_estimated_cost_eur"],
        warnings=merged["warnings"],
        per_image_detail=[SingleImageDiagnosis(**r) for r in per_image_results],
        merge_method=merged["merge_method"],
        model_version=MODEL_VERSION,
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    # 예상 못한 에러를 그냥 500 스택트레이스로 흘려보내지 않고, 백엔드팀이 로그에서
    # 바로 원인을 알 수 있게 명시적인 메시지로 감쌈.
    return JSONResponse(status_code=500, content={"detail": f"서버 내부 오류: {exc}"})
