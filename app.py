import base64
import json
from datetime import datetime

from fastapi import Form
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import io, os, uuid, mido

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse, FileResponse
from pydantic import ValidationError
from rapper import GenerateMeta
from model import *

app = FastAPI()
origins = [
    "http://localhost",
    "http://localhost:3000", # ローカルでの開発用フロントエンドなど
    "http://localhost:8080", # ローカルでの開発用フロントエンドなど
    "https://ayato964.github.io", # あなたのGitHub PagesのURL
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins, # 開発中は "*" ですべて許可するのが簡単。本番では上記のように具体的に指定します。
    allow_credentials=True,
    allow_methods=["*"], # "POST", "GET" など、許可するHTTPメソッド
    allow_headers=["*"], # 許可するHTTPヘッダー
)

CONTROLLER: Optional[ModelController] = None
ROOT_SAVE_DIR = Path("data/saves")


async def midi_save(base: UploadFile, save_path: Path, save_name: str):
    # 2. MIDIファイルの読み込みと保存
    raw = await base.read()
    midi_obj = mido.MidiFile(file=io.BytesIO(raw))

    midi_file_path = os.path.join(save_path, save_name)
    midi_obj.save(midi_file_path)
    return midi_file_path

@app.post("/model_info")
async def model_info():
    return JSONResponse(CONTROLLER.meta)
    

@app.post("/generate")
async def generate(
    past_midi: Optional[UploadFile] = None,
    conditions_midi: Optional[UploadFile] = None,
    future_midi: Optional[UploadFile] = None,
    meta_json: UploadFile = File(...),

):
    print(f"Input: {past_midi is not None}, {conditions_midi is not None}, {future_midi is not None}, {meta_json.filename}")
    allowed_midi_types = {"audio/midi", "audio/x-midi", "application/x-midi", "application/octet-stream"}
    if past_midi is not None:
        if past_midi.content_type not in allowed_midi_types:
            return JSONResponse({"error": "MIDIファイルをアップロードしてください"}, status_code=400)
    if conditions_midi is not None:
        if conditions_midi.content_type not in allowed_midi_types:
            return JSONResponse({"error": "conditions_midiはMIDIファイルをアップロードしてください"}, status_code=400)
    if future_midi is not None:
        if future_midi.content_type not in allowed_midi_types:
            return JSONResponse({"error": "future_midiはMIDIファイルをアップロードしてください"}, status_code=400)
    
    if meta_json.content_type not in {"application/json"}:
        return JSONResponse({"error": "meta_jsonはJSONファイルをアップロードしてください"}, status_code=400)

    try:
        # 1. PydanticモデルでJSONファイルをパース & バリデーション
        try:
            json_content = await meta_json.read()
            meta = GenerateMeta.model_validate(json.loads(json_content))
        except (ValidationError, json.JSONDecodeError) as e:
            return JSONResponse(
                content={"error": "無効な'meta_json'形式です。", "details": json.loads(e.json()) if isinstance(e, ValidationError) else str(e)},
                status_code=422,
            )

        # 2. アップロードされたMIDIファイルを一時保存
        ROOT_SAVE_DIR.mkdir(parents=True, exist_ok=True)

        today = datetime.now().strftime("%Y%m%d")
        hash_id = base64.urlsafe_b64encode(uuid.uuid4().bytes).rstrip(b"=").decode("ascii")[:12]
        save_path = ROOT_SAVE_DIR / today / hash_id
        save_path.mkdir(parents=True, exist_ok=True)

        if past_midi is not None:
            past_midi_path = await midi_save(past_midi, save_path, "past.mid")
        else:
            past_midi_path = None

        if conditions_midi is not None:
            conditions_midi_path = await midi_save(conditions_midi, save_path, "cond.mid")
        else:
            conditions_midi_path = None

        if future_midi is not None:
            future_midi_path = await midi_save(future_midi, save_path, "future.mid")
        else:
            future_midi_path = None

        if CONTROLLER is None:
            return JSONResponse({"error": "モデルが初期化されていません"}, status_code=500)

        # 3. Controllerのgenerateを呼び出し、結果のファイルパスを取得
        result = await CONTROLLER.generate(meta.model_type, past_midi_path, conditions_midi_path, future_midi_path, meta, save_path)

        output_file_path = result.get("output_file")

        # 4. ファイルパスの存在を確認
        if not output_file_path or not os.path.exists(output_file_path):
            return JSONResponse(
                content={"error": "生成されたファイルが見つかりません。"},
                status_code=500,
            )

        # 5. 拡張子に応じてmedia_typeを決定し、FileResponseとして返す
        file_extension = os.path.splitext(output_file_path)[1].lower()
        if file_extension == ".zip":
            media_type = "application/zip"
        elif file_extension == ".mid":
            media_type = "audio/midi"
        elif file_extension == ".txt":
            media_type = "text/plain"
        else:
            media_type = "application/octet-stream"

        return FileResponse(
            path=output_file_path,
            media_type=media_type,
            filename=os.path.basename(output_file_path)
        )

    except Exception as e:
        print(e)
        return JSONResponse({"error": str(e)}, status_code=500)

if __name__ == "__main__":
    print("モデルの初期化を行っています・・・・")
    CONTROLLER = ModelController()
    print("モデルの初期化が完了しました。")
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
