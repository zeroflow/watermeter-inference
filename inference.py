import openvino as ov
import cv2
import numpy as np
from pathlib import Path
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import tempfile
import httpx
import yaml

class Classifier:
    def __init__(self, model_path, classes, label_config_tag, device='GPU'):
        core = ov.Core()
        model = core.read_model(model_path)
        self.compiled = core.compile_model(model, device)
        self.classes = classes
        self.label_config_tag = label_config_tag
    
    def preprocess(self, image_path):
        img = cv2.imread(str(image_path))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (144, 144))
        img = img.astype(np.float32) / 255.0
        img = (img - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
        return img.transpose(2, 0, 1)[np.newaxis, ...]
    
    def predict(self, image_path):
        img = self.preprocess(image_path)
        result = self.compiled([img])[self.compiled.output(0)][0]
        probs = np.exp(result) / np.exp(result).sum()
        idx = probs.argmax()
        return {
            'class': self.classes[idx],
            'confidence': float(probs[idx])
        }

# Load configuration
with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)

inference_config = config['inference']

app = FastAPI()

digits_classifier = Classifier(
    inference_config['digits_model'],
    inference_config['digits_classes'],
    'digit',
    device=inference_config.get('device', 'GPU')
)

arrows_classifier = Classifier(
    inference_config['arrows_model'],
    inference_config['arrows_classes'],
    'arrow_value',
    device=inference_config.get('device', 'GPU')
)

classifiers = {
    'digits': digits_classifier,
    'arrows': arrows_classifier
}

# Label Studio ML Backend models

class SetupRequest(BaseModel):
    project: Optional[str] = None
    schema: Optional[str] = None
    hostname: Optional[str] = None
    access_token: Optional[str] = None

class PredictRequest(BaseModel):
    tasks: list
    project: Optional[str] = None
    label_config: Optional[str] = None
    params: Optional[dict] = None

async def download_image(url: str) -> str:
    """Download image from URL to temp file."""
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        response.raise_for_status()
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp:
            tmp.write(response.content)
            return tmp.name

# Setup endpoints
@app.post("/predict/{model_type}/setup")
async def setup(model_type: str, request: SetupRequest):
    if model_type not in classifiers:
        return JSONResponse({"error": f"Unknown model: {model_type}"}, status_code=404)
    
    clf = classifiers[model_type]
    return {
        "model_version": f"{model_type}-v1",
        "labels": clf.classes
    }

# Predict endpoints for Label Studio
@app.post("/predict/{model_type}/predict")
async def predict_ls(model_type: str, request: PredictRequest):
    if model_type not in classifiers:
        return JSONResponse({"error": f"Unknown model: {model_type}"}, status_code=404)
    
    clf = classifiers[model_type]
    results = []
    
    for task in request.tasks:
        image_url = task.get('data', {}).get('image')
        if not image_url:
            results.append({"result": [], "score": 0.0})
            continue
        
        try:
            tmp_path = await download_image(image_url)
            pred = clf.predict(tmp_path)
            Path(tmp_path).unlink()
            
            # Different format for Number vs Choices
            if model_type == 'arrows':
                result_item = {
                    "from_name": clf.label_config_tag,
                    "to_name": "image",
                    "type": "number",
                    "value": {
                        "number": float(pred['class'])
                    }
                }
            else:
                result_item = {
                    "from_name": clf.label_config_tag,
                    "to_name": "image",
                    "type": "choices",
                    "value": {
                        "choices": [pred['class']]
                    }
                }
            
            results.append({
                "result": [result_item],
                "score": pred['confidence']
            })
        except Exception as e:
            results.append({"result": [], "score": 0.0, "error": str(e)})
    
    return {"results": results}

# Health endpoints
@app.get("/health")
def health():
    return {"status": "ok", "models": list(classifiers.keys()), "device": "GPU"}

@app.get("/predict/{model_type}/health")
def model_health(model_type: str):
    if model_type not in classifiers:
        return JSONResponse({"error": f"Unknown model: {model_type}"}, status_code=404)
    return {"status": "ok", "model": model_type, "device": "GPU"}

# Direct prediction endpoint (your original)
@app.post("/predict/{model_type}")
async def predict_direct(model_type: str, file: UploadFile = File(...)):
    if model_type not in classifiers:
        return JSONResponse({"error": f"Unknown model: {model_type}"}, status_code=404)
    
    with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    
    result = classifiers[model_type].predict(tmp_path)
    Path(tmp_path).unlink()
    
    return JSONResponse({**result, 'model': model_type})