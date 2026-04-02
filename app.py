import base64
import io
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import mido
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError

from model import ModelController
from rapper import GenerateMeta

CONTROLLER: Optional[ModelController] = None
ROOT_SAVE_DIR = Path("data/saves")


@asynccontextmanager
async def lifespan(_: FastAPI):
    global CONTROLLER
    if CONTROLLER is None:
        print("モデルの初期化を行っています・・・・")
        CONTROLLER = ModelController()
        print("モデルの初期化が完了しました。")
    yield


app = FastAPI(lifespan=lifespan)

origins = [
    "http://localhost",
    "http://localhost:3000",
    "http://localhost:8080",
    "https://ayato964.github.io",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def midi_save(upload: UploadFile, save_path: Path, save_name: str) -> str:
    raw = await upload.read()
    midi_obj = mido.MidiFile(file=io.BytesIO(raw))
    midi_file_path = save_path / save_name
    midi_obj.save(str(midi_file_path))
    return str(midi_file_path)


@app.post("/model_info")
async def model_info():
    if CONTROLLER is None:
        return JSONResponse({"error": "モデルが初期化されていません"}, status_code=503)
    return JSONResponse(CONTROLLER.meta)


@app.post("/generate")
async def generate(
    midi: Optional[UploadFile] = File(None),
    past_midi: Optional[UploadFile] = File(None),
    conditions_midi: Optional[UploadFile] = File(None),
    future_midi: Optional[UploadFile] = File(None),
    meta_json: UploadFile = File(...),
):
    # 旧README互換: midi が与えられたら conditions_midi として扱う
    if midi is not None:
        if conditions_midi is not None:
            return JSONResponse(
                {"error": "midi と conditions_midi を同時には指定できません"},
                status_code=400,
            )
        conditions_midi = midi

    print(
        f"Input: past={past_midi is not None}, cond={conditions_midi is not None}, "
        f"future={future_midi is not None}, meta={meta_json.filename}"
    )

    allowed_midi_types = {
        "audio/midi",
        "audio/x-midi",
        "application/x-midi",
        "application/octet-stream",
    }
    allowed_json_types = {
        "application/json",
        "text/json",
        "application/octet-stream",
    }

    for name, upload in (
        ("past_midi", past_midi),
        ("conditions_midi", conditions_midi),
        ("future_midi", future_midi),
    ):
        if upload is not None and upload.content_type not in allowed_midi_types:
            return JSONResponse(
                {"error": f"{name} はMIDIファイルをアップロードしてください"},
                status_code=400,
            )

    if meta_json.content_type not in allowed_json_types:
        return JSONResponse(
            {"error": "meta_json は JSON ファイルをアップロードしてください"},
            status_code=400,
        )

    try:
        try:
            json_content = await meta_json.read()
            meta = GenerateMeta.model_validate(json.loads(json_content))
        except (ValidationError, json.JSONDecodeError) as e:
            details = json.loads(e.json()) if isinstance(e, ValidationError) else str(e)
            return JSONResponse(
                content={"error": "無効な meta_json 形式です。", "details": details},
                status_code=422,
            )

        ROOT_SAVE_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y%m%d")
        hash_id = base64.urlsafe_b64encode(uuid.uuid4().bytes).rstrip(b"=").decode("ascii")[:12]
        save_path = ROOT_SAVE_DIR / today / hash_id
        save_path.mkdir(parents=True, exist_ok=True)

        past_midi_path = await midi_save(past_midi, save_path, "past.mid") if past_midi is not None else None
        conditions_midi_path = (
            await midi_save(conditions_midi, save_path, "cond.mid")
            if conditions_midi is not None
            else None
        )
        future_midi_path = (
            await midi_save(future_midi, save_path, "future.mid")
            if future_midi is not None
            else None
        )

        if CONTROLLER is None:
            return JSONResponse({"error": "モデルが初期化されていません"}, status_code=503)

        result = await CONTROLLER.generate(
            meta.model_type,
            past_midi_path,
            conditions_midi_path,
            future_midi_path,
            meta,
            save_path,
        )

        output_file_path = result.get("output_file")
        is_json_result = result.get("is_json_result", False)

        if is_json_result and output_file_path and os.path.exists(output_file_path):
            with open(output_file_path, "r", encoding="utf-8") as f:
                json_data = json.load(f)
            return JSONResponse(content={"result": "success", "data": json_data})

        if not output_file_path or not os.path.exists(output_file_path):
            return JSONResponse(
                content={"error": "生成されたファイルが見つかりません。"},
                status_code=500,
            )

        file_extension = os.path.splitext(output_file_path)[1].lower()
        if file_extension == ".zip":
            media_type = "application/zip"
        elif file_extension == ".mid":
            media_type = "audio/midi"
        elif file_extension == ".txt":
            media_type = "text/plain"
        elif file_extension == ".json":
            media_type = "application/json"
        else:
            media_type = "application/octet-stream"

        return FileResponse(
            path=output_file_path,
            media_type=media_type,
            filename=os.path.basename(output_file_path),
        )

    except Exception as e:
        print(e)
        return JSONResponse({"error": str(e)}, status_code=500)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)