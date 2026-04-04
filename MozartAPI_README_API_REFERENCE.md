# MozartAPI API Reference

このドキュメントは、修正版 `MozartAPI` の現在の実装に対応した API リファレンスです。  
対象実装は `mozartapi_patch.zip` に含まれる以下の構成を前提とします。

- `app.py`
- `model.py`
- `rapper.py`
- `models/mortm/mortm45.py`

---

## 1. 概要

- **Base URL**: `http://<host>:8000`
- **認証**: なし
- **レスポンス形式**:
  - モデル一覧: JSON
  - 生成結果:
    - 単一 MIDI: バイナリ (`audio/midi`)
    - 複数 MIDI: ZIP (`application/zip`)
    - JSON 系タスク: JSON
- **CORS 許可 Origin**:
  - `http://localhost`
  - `http://localhost:3000`
  - `http://localhost:8080`
  - `https://ayato964.github.io`

---

## 2. エンドポイント一覧

### `POST /model_info`

利用可能なモデル一覧を返します。

#### Request

Body なし。

#### Response

`200 OK`

```json
{
  "0": {
    "model_name": "MORTM4.5D-Lite",
    "description": "...",
    "tag": {
      "model": "pretrained",
      "type": "mortm"
    },
    "rule": {
      "input_midi": true
    },
    "model_folder_path": "/abs/path/to/data/models/MORTM4.5D-Lite"
  }
}
```

#### 備考

- `data/models/*/data.json` の内容をほぼそのまま返し、`model_folder_path` を追加します。
- キー `"0"`, `"1"` などは内部インデックスです。

---

### `POST /generate`

音楽生成または JSON 系推論を実行します。

#### Content-Type

`multipart/form-data`

#### Form fields

##### 必須

- `meta_json`: JSON ファイル

##### 任意

- `midi`: 旧互換入力。**`conditions_midi` の別名**
- `past_midi`: 過去文脈 MIDI
- `conditions_midi`: 条件 MIDI
- `future_midi`: 未来文脈 MIDI

#### 注意

- `midi` と `conditions_midi` は**同時指定不可**
- MIDI ファイルとして受理する content type:
  - `audio/midi`
  - `audio/x-midi`
  - `application/x-midi`
  - `application/octet-stream`
- `meta_json` として受理する content type:
  - `application/json`
  - `text/json`
  - `application/octet-stream`

---

## 3. `meta_json` スキーマ

```json
{
  "model_type": "MORTM4.5D-Lite",
  "program": ["PIANO"],
  "task": "Meta2MIDI",
  "key": "CM",
  "num_gems": 1,
  "genfield_measure": 8,
  "gen_note_dense": {"PIANO": 4},
  "p": 0.95,
  "temperature": 1.0,
  "chord_item": null,
  "chord_times": null,
  "split_measure": 999,
  "ai_continue_mode": false
}
```

### 各フィールド

- `model_type: string`  
  使用するモデル名。表記揺れはある程度吸収されます。  
  例: `MORTM4.5D-Lite`, `MORTM4.5D-LITE`

- `program: List[str | int]`  
  対象楽器。内部で正規化されます。  
  現在対応:
  - `PIANO`
  - `SAX`

  数値指定も可:
  - `0..6` → `PIANO`
  - `64..68` → `SAX`

- `tempo: int`  
  0 より大きい必要があります。

- `task: string`  
  推奨タスク名 (対応エイリアス):
  - `Meta2MIDI` (旧: `Prompt2MIDI`, `generate`, `melodygen`)
  - `MIDI2Meta` (旧: `MetaGen`)
  - `Chord2MIDI`
  - `MIDI2Chord`

- `key: string | null`  
  例: `CM`, `Am`

- `num_gems: int = 1`  
  生成本数。`>= 1`

- `genfield_measure: int = 8`  
  生成小節数。内部トークン化時には **1〜8 に clamp** されます。

- `gen_note_dense: int | object = {"PIANO": 4}`  
  音密度。内部で **1〜10 に clamp**。  
  単一値でも辞書でも可。多楽器時は辞書推奨。

- `p: float = 0.95`  
  top-p。`0 < p <= 1`

- `temperature: float = 1.0`  
  `> 0`

- `chord_item: List[str] | null`  
  コード列。`Chord2MIDI` 系で使用。

- `chord_times: List[float] | null`  
  各コードの開始時刻。`chord_item` と同時指定必須。

- `split_measure: int = 999`  
  現行 API では実質予約的。

- `ai_continue_mode: bool = false`  
  現行修正版の主要経路では積極使用しません。

### バリデーション

- `tempo <= 0` はエラー
- `num_gems <= 0` はエラー
- `genfield_measure <= 0` はエラー
- `p` が `(0,1]` 外ならエラー
- `temperature <= 0` はエラー
- `chord_item` と `chord_times` は**両方指定**または**両方省略**
- `chord_item` と `chord_times` の長さは一致必須

---

## 4. タスク仕様

### 4.1 `Meta2MIDI`

メタ情報から旋律を生成します。最も基本的な経路です。

#### 最低要件

- `task = "Meta2MIDI"`
- `program`
- `tempo`

#### 任意条件

- `past_midi`
- `conditions_midi`
- `future_midi`
- `chord_item` + `chord_times`

#### 出力

- `num_gems = 1` → 単一 `.mid`
- `num_gems > 1` → `.zip`

---

### 4.2 `Chord2MIDI`

コード進行を条件に旋律を生成します。

#### 最低要件

- `task = "Chord2MIDI"`
- `chord_item`
- `chord_times`

#### 任意条件

- `past_midi`
- `conditions_midi`
- `future_midi`

#### 出力

- MIDI または ZIP

---

### 4.3 `MIDI2Chord`

入力 MIDI からコード進行を推定します。

#### 最低要件

- `task = "MIDI2Chord"`
- `conditions_midi`

#### 禁止条件

- `past_midi`
- `future_midi`
- `chord_item`
- `chord_times`

#### 出力

`200 OK`

```json
{
  "result": "success",
  "data": [
    [
      {"time": 0.0, "chord": "Cmaj7"},
      {"time": 2.0, "chord": "Dm7"}
    ]
  ]
}
```

---

### 4.4 `MIDI2Meta`

入力 MIDI からメタ情報を推定します。

#### 最低要件

- `task = "MIDI2Meta"`
- `conditions_midi`

#### 禁止条件

- `past_midi`
- `future_midi`
- `chord_item`
- `chord_times`

#### 出力

`200 OK`

```json
{
  "result": "success",
  "data": [
    {
      "key": "CM",
      "instruments": ["PIANO"],
      "note_density": {
        "PIANO": "4"
      },
      "gen_measure_count": "8"
    }
  ]
}
```

---

## 5. モデル互換性

### `tag.model == "pretrained"`

対応:

- `Meta2MIDI`
- `Chord2MIDI`
- `MIDI2Chord`
- `MIDI2Meta`

### `tag.model == "generation"`

対応:

- `Meta2MIDI`
- `Chord2MIDI`

非対応:

- `MIDI2Chord`
- `MIDI2Meta`

---

## 6. `/generate` のレスポンス仕様

### A. 単一ファイル生成

- Status: `200 OK`
- Body: `.mid`
- Media type: `audio/midi`

### B. 複数ファイル生成

- Status: `200 OK`
- Body: `.zip`
- Media type: `application/zip`

### C. JSON 系タスク

- Status: `200 OK`
- Body:

```json
{
  "result": "success",
  "data": [...]
}
```

---

## 7. エラー仕様

### `400 Bad Request`

例:

- `midi` と `conditions_midi` を同時指定
- MIDI でないファイルを `past_midi` 等に送信
- `meta_json` が JSON として不正

```json
{
  "error": "conditions_midi はMIDIファイルをアップロードしてください"
}
```

### `422 Unprocessable Entity`

`meta_json` のバリデーション失敗。

```json
{
  "error": "無効な meta_json 形式です。",
  "details": [...]
}
```

### `503 Service Unavailable`

モデル未初期化。

```json
{
  "error": "モデルが初期化されていません"
}
```

### `500 Internal Server Error`

推論失敗、出力未生成、モデル不整合など。

```json
{
  "error": "..."
}
```

---

## 8. cURL 例

### 8.1 モデル一覧

```bash
curl -X POST "http://localhost:8000/model_info"
```

### 8.2 普通の旋律生成

```bash
curl -X POST "http://localhost:8000/generate" \
  -F "meta_json=@meta.json;type=application/json" \
  -o output.mid
```

`meta.json`

```json
{
  "model_type": "MORTM4.5D-Lite",
  "program": ["PIANO"],
  "tempo": 120,
  "task": "Meta2MIDI",
  "key": "CM",
  "genfield_measure": 8,
  "gen_note_dense": {"PIANO": 4},
  "num_gems": 1,
  "p": 0.95,
  "temperature": 1.0
}
```

### 8.3 コードから旋律生成

```bash
curl -X POST "http://localhost:8000/generate" \
  -F "meta_json=@meta_chord.json;type=application/json" \
  -o output.mid
```

### 8.4 MIDI からコード推定

```bash
curl -X POST "http://localhost:8000/generate" \
  -F "conditions_midi=@input.mid" \
  -F "meta_json=@meta_midi2chord.json;type=application/json"
```

### 8.5 MIDI からメタ情報推定

```bash
curl -X POST "http://localhost:8000/generate" \
  -F "conditions_midi=@input.mid" \
  -F "meta_json=@meta_midi2meta.json;type=application/json"
```

---

## 9. 実装上の重要注意

1. `program` は**現在 PIANO / SAX のみ**です。  
2. `genfield_measure` は受理値が 16 でも、内部トークンとしては **8 に丸められます**。  
3. `midi` は旧互換であり、**新規実装では `conditions_midi` を正規入力**とみなすべきです。  
4. JSON 系タスクはファイルではなく **JSON 本文**を返します。  
5. `/model_info` は `GET` ではなく **`POST`** です。
