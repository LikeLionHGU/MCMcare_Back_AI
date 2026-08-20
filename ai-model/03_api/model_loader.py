"""
YOLOv8 가중치 로딩 + 추론 래퍼.

⚠️ 가중치 파일 관련 (확인됨):
  이 코드를 만든 샌드박스는 인터넷이 허용목록 방식으로 막혀있어서 ultralytics/torch
  설치 자체가 불가능했고, 그래서 이 세션 안에서는 02_train_yolo.py를 실제로 돌려서
  진짜 .pt 가중치를 만들어내지 못했음 (가짜 가중치로 대체하지 않음 - 더미 데이터 금지 원칙).
  MODEL_PATH에 실제 학습된 가중치가 없으면 이 모듈은 model_loaded=False 상태로 남고,
  /diagnose 호출 시 503으로 명확히 실패함 (엉뚱한 값을 지어내서 200으로 응답하지 않음).

  02_train_yolo.py를 인터넷 되는 환경(Colab/로컬/사내서버)에서 돌려 best.pt를 얻은 뒤
  MODEL_PATH 환경변수나 아래 DEFAULT_MODEL_PATH를 그 경로로 지정하면 정상 동작함.
"""

import os
from pathlib import Path
from typing import List, Optional, Tuple

from severity import Detection

THIS_DIR = Path(__file__).resolve().parent
MODEL_DIR = THIS_DIR.parent
DEFAULT_MODEL_PATH = MODEL_DIR / "02_weights" / "main_run" / "weights" / "best.pt"
MODEL_PATH = Path(os.environ.get("MODEL_PATH", str(DEFAULT_MODEL_PATH)))

CLASSES = [
    "가방전체(손상아님)",
    "균열/파손",
    "변형",
    "손상(세부미상)",
    "찢김/파열",
]


class ModelNotLoadedError(RuntimeError):
    pass


class DamageDetector:
    def __init__(self, model_path: Path = MODEL_PATH, conf_threshold: float = 0.25):
        self.model_path = model_path
        self.conf_threshold = conf_threshold
        self._model = None
        self._load_error: Optional[str] = None
        self._try_load()

    def _try_load(self):
        if not self.model_path.exists():
            self._load_error = (
                f"가중치 파일이 없음: {self.model_path}. "
                "02_train_yolo.py를 인터넷 되는 환경에서 먼저 실행해서 best.pt를 만들어야 함."
            )
            return
        try:
            from ultralytics import YOLO
        except ImportError:
            self._load_error = (
                "ultralytics 패키지가 설치 안 됨. `pip install -r requirements.txt` 먼저 실행할 것."
            )
            return
        try:
            self._model = YOLO(str(self.model_path))
        except Exception as e:  # noqa: BLE001 - 로딩 실패 사유를 그대로 노출하기 위함
            self._load_error = f"모델 로딩 실패: {e}"

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def load_error(self) -> Optional[str]:
        return self._load_error

    def predict(self, image_path: str) -> Tuple[List[Detection], int, int]:
        if not self.is_loaded:
            raise ModelNotLoadedError(self._load_error or "모델이 로드되지 않음")

        results = self._model.predict(source=image_path, conf=self.conf_threshold, verbose=False)
        result = results[0]
        img_h, img_w = result.orig_shape

        detections = []
        for box in result.boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            x_center, y_center, w, h = [float(v) for v in box.xywhn[0]]  # 정규화 좌표
            detections.append(
                Detection(
                    class_name=CLASSES[cls_id],
                    confidence=conf,
                    x_center=x_center,
                    y_center=y_center,
                    width=w,
                    height=h,
                )
            )
        return detections, img_w, img_h
