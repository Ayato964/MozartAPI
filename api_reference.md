# MORTM API Reference

This document describes the available endpoints and parameters for the MORTM API.

## 1. Model Info Endpoint

Returns metadata for a specific model.

- **URL**: `/model-info/`
- **Method**: `GET`
- **Response**: `application/json`
  - Success: `{"model_name": "...", "tag": {...}, ...}`
  - Error: `{"error": "Model not found"}`

---

## 2. Generate Endpoint

Generates MIDI data, chord progressions, or metadata based on the specified task and constraints.

- **URL**: `/generate`
- **Method**: `POST`
- **Content-Type**: `multipart/form-data`

### Parameters

| Name | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `model_name` | string | (required) | The model to use (e.g., `MORTM4.5D-Lite`). |
| [task](file:///home/takaaki-nagoshi/PycharmProjects/MORTM_API2/MORTM_REPO/mortm/utils/generate.py#108-150) | string | `Prompt2MIDI` | Task type: `Prompt2MIDI`, `Chord2MIDI`, `MIDI2Chord`, `MetaGen`. |
| [text](file:///home/takaaki-nagoshi/PycharmProjects/MORTM_API2/models/mortm/mortm45.py#216-238) | string | `""` | Text prompt (currently for logging/metadata). |
| [key](file:///home/takaaki-nagoshi/PycharmProjects/MORTM/mortm/utils/convert.py#576-635) | string | `None` | Key of the music (e.g., `CM`, `Am`, `F#M`). |
| [program](file:///home/takaaki-nagoshi/PycharmProjects/MORTM/.venv/lib/python3.12/site-packages/mortm/utils/convert.py#91-100) | list[string] | `["PIANO"]` | List of instrument names (e.g., `PIANO`, `SAX`). |
| `note_density` | int | `5` | Desired note density (1-10). |
| `genfield_measure`| int | `4` | Number of measures to generate. |
| `num_gems` | int | `1` | Number of variants to generate. |
| `temperature` | float | `1.0` | Sampling temperature (higher = more random). |
| `p` | float | `0.95` | Top-p sampling threshold. |
| `chord_item` | list[string] | `None` | List of chord symbols (for `Chord2MIDI`). |
| `chord_times` | list[float] | `None` | Timestamp (seconds) for each chord in `chord_item`. |
| `past_midi` | file | `None` | Past context MIDI file. |
| `const_midi` | file | `None` | Constraint context MIDI file (required for `MIDI2Chord`, `MetaGen`). |
| `future_midi` | file | `None` | Future context MIDI file. |
| `ai_continue_mode` | bool | `False` | Whether to continue from the provided context. |

### Response Types

#### 1. MIDI File (`application/x-midi`)
Returned for generative tasks:
- `Prompt2MIDI`
- `Chord2MIDI`

#### 2. JSON Data (`application/json`)
Returned for inference tasks or errors:

**MIDI2Chord Result:**
```json
{
  "result": "success",
  "data": [
    [
      { "time": 0.0, "chord": "C" },
      { "time": 4.0, "chord": "G" }
    ]
  ]
}
```

**MetaGen Result:**
```json
{
  "result": "success",
  "data": [
    {
      "key": "CM",
      "instruments": ["PIANO"],
      "note_density": { "PIANO": "5" },
      "gen_measure_count": "4"
    }
  ]
}
```

---

## Task Summary Table

| Task | Input Requirement | Primary Output |
| :--- | :--- | :--- |
| **Prompt2MIDI** | None | MIDI File |
| **Chord2MIDI**  | `chord_item`, `chord_times` | MIDI File |
| **MIDI2Chord**  | `const_midi` | JSON (Chords) |
| **MetaGen**     | `const_midi` | JSON (Metadata) |
