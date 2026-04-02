import os
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import torch
from torch.nn.utils.rnn import pad_sequence

from rapper import AbstractModelRapper, GenerateMeta
from mortm.models.mortm import MORTM, MORTMArgs
from mortm.models.modules.progress import _DefaultLearningProgress
from mortm.train.tokenizer import (
    TO_MUSIC,
    TO_TOKEN,
    Tokenizer,
    get_token_converter_pro,
    omega_converter,
)
from mortm.utils.convert import MIDIConverter, MetaData2Chord
from mortm.utils.de_convert import ct_token_to_midi


_TASK_ALIASES = {
    "prompt2midi": "Prompt2MIDI",
    "generate": "Prompt2MIDI",
    "melodygen": "Prompt2MIDI",
    "chord2midi": "Chord2MIDI",
    "midi2chord": "MIDI2Chord",
    "metagen": "MetaGen",
}


class MORTM45Rapper(AbstractModelRapper):
    def _load_model(self):
        model_path = self.meta["model_folder_path"]
        config_path = os.path.join(model_path, "config.json")
        model_pth_path = os.path.join(model_path, "model.pth")

        print(f"Loading model: {self.meta['model_name']} from {model_path}")
        args = MORTMArgs(config_path)
        progress = _DefaultLearningProgress()
        model: MORTM = MORTM(args, progress).to(progress.get_device())

        state = torch.load(model_pth_path, map_location=progress.get_device())
        missing, unexpected = model.load_state_dict(state, strict=False)
        if missing:
            print(f"[WARN] Missing keys: {missing}")
        if unexpected:
            print(f"[WARN] Unexpected keys: {unexpected}")
        model.eval()
        return model

    # ------------------------------------------------------------------
    # normalization helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_task(task: Optional[str]) -> str:
        if task is None:
            return "Prompt2MIDI"
        key = "".join(ch for ch in task if ch.isalnum()).lower()
        if key not in _TASK_ALIASES:
            raise ValueError(f"Unsupported task: {task}")
        return _TASK_ALIASES[key]

    @staticmethod
    def _normalize_program_name(value: Union[str, int]) -> str:
        if isinstance(value, int):
            if 0 <= value <= 6:
                return "PIANO"
            if 64 <= value <= 68:
                return "SAX"
            raise ValueError(
                f"Unsupported MIDI program id: {value}. "
                f"現在のAPIは PIANO(0-6) / SAX(64-68) のみ対応です。"
            )

        text = str(value).strip()
        if text.isdigit():
            return MORTM45Rapper._normalize_program_name(int(text))

        upper = text.upper()
        aliases = {
            "PIANO": "PIANO",
            "ACOUSTIC_GRAND_PIANO": "PIANO",
            "GRANDPIANO": "PIANO",
            "SAX": "SAX",
            "ALTO_SAX": "SAX",
            "ALTOSAX": "SAX",
            "TENOR_SAX": "SAX",
            "TENORSAX": "SAX",
        }
        if upper not in aliases:
            raise ValueError(
                f"Unsupported program token: {value}. "
                f"現在のAPIは PIANO / SAX のみ対応です。"
            )
        return aliases[upper]

    def _normalize_programs(self, programs: Sequence[Union[str, int]]) -> List[str]:
        normalized = [self._normalize_program_name(p) for p in programs]
        deduped: List[str] = []
        for p in normalized:
            if p not in deduped:
                deduped.append(p)
        if not deduped:
            raise ValueError("program が空です")
        return deduped

    @staticmethod
    def _clamp_measure_count(value: int) -> int:
        return max(1, min(int(value), 8))

    @staticmethod
    def _clamp_density(value: int) -> int:
        return max(1, min(int(value), 10))

    def _resolve_density(self, meta: GenerateMeta, program_name: str) -> int:
        dense = meta.gen_note_dense
        if isinstance(dense, dict):
            candidates = [program_name, program_name.upper(), program_name.lower()]
            for key in candidates:
                if key in dense:
                    return self._clamp_density(dense[key])
            if len(dense) == 1:
                return self._clamp_density(next(iter(dense.values())))
            raise ValueError(
                f"gen_note_dense に {program_name} の設定がありません。"
                f"例: {{\"{program_name}\": 4}}"
            )
        return self._clamp_density(dense)

    # ------------------------------------------------------------------
    # prompt builders
    # ------------------------------------------------------------------
    def _build_system_prompt(
        self,
        tokenizer: Tokenizer,
        meta: GenerateMeta,
        program_names: List[str],
        *,
        include_dense: bool,
        include_measure_count: bool,
    ) -> np.ndarray:
        prompt: List[int] = [tokenizer.get("<EOS>"), tokenizer.get("<SYSTEM>")]

        for program_name in program_names:
            prompt.append(tokenizer.get(f"<INST_{program_name}>"))
            if include_dense:
                dense = self._resolve_density(meta, program_name)
                prompt.append(tokenizer.get(f"<NOTE_DENSE_{dense}>"))

        if include_measure_count:
            prompt.append(
                tokenizer.get(
                    f"<GEN_MEASURE_COUNT_{self._clamp_measure_count(meta.genfield_measure)}>")
            )

        if getattr(meta, "key", None):
            prompt.append(tokenizer.get(f"k_{meta.key}"))

        prompt.append(tokenizer.get("<TAG_END>"))
        return np.asarray(prompt, dtype=np.int64)

    def _load_midi_node_dict(
        self,
        tokenizer: Tokenizer,
        midi_path: str,
        program_names: List[str],
        key: Optional[str],
    ) -> Dict[str, np.ndarray]:
        converter = MIDIConverter(
            tokenizer,
            os.path.dirname(midi_path),
            os.path.basename(midi_path),
            program_list=program_names,
            key=key,
            use_midi2seq=True,
            use_midi2seq_with_chord=False,
        )
        converter.convert()
        if converter.is_error:
            raise ValueError(converter.error_reason)
        if converter.midi2seq is None:
            raise ValueError("MIDI2Seq converter was not initialized")
        return converter.midi2seq.aya_node

    def get_context(self, tokenizer: Tokenizer, node_dict: dict, key_token: str) -> List[int]:
        seq = [tokenizer.get(key_token)]

        cr_range = tokenizer.get_length_tuple("CR")
        cq_range = tokenizer.get_length_tuple("CQ")
        cb_range = tokenizer.get_length_tuple("CB")

        for program_name, inst in node_dict.items():
            seq.append(tokenizer.get(f"<INST_{program_name}>"))
            clean_inst = []
            for t_id in inst[:-1]:
                if (
                    cr_range[0] <= t_id <= cr_range[1]
                    or cq_range[0] <= t_id <= cq_range[1]
                    or cb_range[0] <= t_id <= cb_range[1]
                ):
                    continue
                clean_inst.append(int(t_id))
            seq.extend(clean_inst)
            seq.append(tokenizer.get("<ESEQ>"))
        seq.append(tokenizer.get("<TAG_END>"))
        return seq

    def _build_midi_context(
        self,
        tokenizer: Tokenizer,
        midi_path: str,
        key_token: str,
        program_names: List[str],
        key: Optional[str],
    ) -> np.ndarray:
        node_dict = self._load_midi_node_dict(tokenizer, midi_path, program_names, key)
        return np.asarray(self.get_context(tokenizer, node_dict, key_token), dtype=np.int64)

    def _build_const_chord_prompt(self, tokenizer: Tokenizer, meta: GenerateMeta) -> np.ndarray:
        if meta.chord_item is None or meta.chord_times is None:
            raise ValueError("Chord2MIDI 系では chord_item と chord_times が必要です")

        chord = MetaData2Chord(
            tokenizer,
            meta.key,
            meta.chord_item,
            meta.chord_times,
            meta.tempo,
            None,
            None,
            999,
            False,
        )
        chord.convert()
        if len(chord.aya_node) <= 1:
            raise ValueError("コード進行をトークン化できませんでした")

        chord_tokens = np.asarray(chord.aya_node[1], dtype=np.int64)
        return np.concatenate(
            [
                np.asarray([tokenizer.get("<CONST_C>")], dtype=np.int64),
                chord_tokens,
                np.asarray([tokenizer.get("<ESEQ>"), tokenizer.get("<TAG_END>")], dtype=np.int64),
            ]
        )

    def _build_pretrained_prompt(
        self,
        tokenizer: Tokenizer,
        past_midi: Optional[str],
        const_midi: Optional[str],
        future_midi: Optional[str],
        meta: GenerateMeta,
        program_names: List[str],
    ) -> np.ndarray:
        task = self._normalize_task(meta.task)

        if task == "MetaGen":
            if const_midi is None:
                raise ValueError("MetaGen では conditions_midi が必須です")
            if past_midi is not None or future_midi is not None or meta.chord_item is not None:
                raise ValueError("MetaGen は conditions_midi のみ対応です")

            prompt = np.asarray([tokenizer.get("<EOS>")], dtype=np.int64)
            const_seq = self._build_midi_context(tokenizer, const_midi, "<CONST_M>", program_names, meta.key)
            return np.concatenate([prompt, const_seq, np.asarray([tokenizer.get("<META>")], dtype=np.int64)])

        if task == "MIDI2Chord":
            if const_midi is None:
                raise ValueError("MIDI2Chord では conditions_midi が必須です")
            if past_midi is not None or future_midi is not None or meta.chord_item is not None:
                raise ValueError("MIDI2Chord は conditions_midi のみ対応です")

        include_dense = task != "MIDI2Chord"
        include_measure_count = future_midi is None
        prompt = self._build_system_prompt(
            tokenizer,
            meta,
            program_names,
            include_dense=include_dense,
            include_measure_count=include_measure_count,
        )

        if past_midi is not None:
            prompt = np.concatenate(
                [prompt, self._build_midi_context(tokenizer, past_midi, "<PAST_M>", program_names, meta.key)]
            )

        if future_midi is not None:
            prompt = np.concatenate(
                [prompt, self._build_midi_context(tokenizer, future_midi, "<FUTURE_M>", program_names, meta.key)]
            )

        if const_midi is not None:
            prompt = np.concatenate(
                [prompt, self._build_midi_context(tokenizer, const_midi, "<CONST_M>", program_names, meta.key)]
            )

        if meta.chord_item is not None:
            prompt = np.concatenate([prompt, self._build_const_chord_prompt(tokenizer, meta)])

        start_token = "<CGEN>" if task == "MIDI2Chord" else "<MGEN>"
        return np.concatenate([prompt, np.asarray([tokenizer.get(start_token)], dtype=np.int64)])

    # ------------------------------------------------------------------
    # preprocessing
    # ------------------------------------------------------------------
    def preprocessing(self, past_midi, const_midi, future_midi, meta: GenerateMeta):
        program_names = self._normalize_programs(meta.program)
        task = self._normalize_task(meta.task)
        model_tag = self.meta["tag"]["model"]

        if model_tag == "pretrained":
            tokenizer = Tokenizer(get_token_converter_pro(TO_TOKEN))
            sequences = []
            for _ in range(meta.num_gems):
                sequences.append(
                    self._build_pretrained_prompt(
                        tokenizer,
                        past_midi,
                        const_midi,
                        future_midi,
                        meta,
                        program_names,
                    )
                )
            return {
                "meta": meta,
                "task": task,
                "sequence": np.stack(sequences, axis=0),
                "tokenizer": tokenizer,
            }

        if model_tag == "generation":
            if task in {"MIDI2Chord", "MetaGen"}:
                raise ValueError(f"{self.meta['model_name']} は {task} をサポートしていません")

            tokenizer = Tokenizer(omega_converter(TO_TOKEN))
            sequences = []
            for _ in range(meta.num_gems):
                prompt = self._build_system_prompt(
                    tokenizer,
                    meta,
                    program_names,
                    include_dense=True,
                    include_measure_count=(future_midi is None),
                )

                if past_midi is not None:
                    prompt = np.concatenate(
                        [prompt, self._build_midi_context(tokenizer, past_midi, "<PAST_M>", program_names, meta.key)]
                    )
                if future_midi is not None:
                    prompt = np.concatenate(
                        [prompt, self._build_midi_context(tokenizer, future_midi, "<FUTURE_M>", program_names, meta.key)]
                    )
                if const_midi is not None:
                    prompt = np.concatenate(
                        [prompt, self._build_midi_context(tokenizer, const_midi, "<CONST_M>", program_names, meta.key)]
                    )
                if meta.chord_item is not None:
                    prompt = np.concatenate([prompt, self._build_const_chord_prompt(tokenizer, meta)])

                prompt = np.concatenate([prompt, np.asarray([tokenizer.get("<MGEN>")], dtype=np.int64)])
                sequences.append(torch.tensor(prompt, dtype=torch.long, device="cuda"))

            return {
                "meta": meta,
                "task": task,
                "sequence": sequences,
                "tokenizer": tokenizer,
            }

        raise ValueError(f"Unsupported model tag: {model_tag}")

    # ------------------------------------------------------------------
    # generation
    # ------------------------------------------------------------------
    @torch.inference_mode()
    def _sample_meta_sequences(
        self,
        tokenizer: Tokenizer,
        src: Union[np.ndarray, torch.Tensor],
        *,
        p: float,
        temperature: float,
    ) -> List[np.ndarray]:
        device = self.model.progress.get_device()

        if isinstance(src, np.ndarray):
            src_tensor = torch.tensor(src, dtype=torch.long, device=device)
        else:
            src_tensor = src.to(device=device, dtype=torch.long)

        if src_tensor.dim() == 1:
            src_tensor = src_tensor.unsqueeze(0)

        padding_id = tokenizer.get("<PAD>")
        padding_mask = src_tensor != padding_id

        logits = self.model.forward(
            src_tensor,
            padding_mask=padding_mask,
            is_causal=True,
            is_save_cache=True,
        )
        next_tokens = self.model.top_p_sampling(logits[:, -1, :], p=p, temperature=temperature)
        all_tokens = torch.cat([src_tensor, next_tokens.unsqueeze(1)], dim=1)

        max_steps = int(self.model.args.position_length)
        step = 0
        while True:
            logits = self.model.forward(
                next_tokens.unsqueeze(1),
                padding_mask=None,
                is_causal=True,
                is_save_cache=True,
            )
            next_tokens = self.model.top_p_sampling(logits.squeeze(1), p=p, temperature=temperature)
            all_tokens = torch.cat([all_tokens, next_tokens.unsqueeze(1)], dim=1)
            step += 1
            if self.model.is_end_point(all_tokens, [tokenizer.get("<TE>")]) or step >= max_steps:
                break

        outputs: List[np.ndarray] = []
        meta_id = tokenizer.get("<META>")
        te_id = tokenizer.get("<TE>")
        for row in all_tokens:
            row = row[row != padding_id]
            meta_pos = (row == meta_id).nonzero(as_tuple=True)[0]
            if len(meta_pos) == 0:
                raise ValueError("<META> 開始位置を検出できませんでした")
            start = int(meta_pos[-1].item())
            te_pos = (row == te_id).nonzero(as_tuple=True)[0]
            end = int(te_pos[0].item()) if len(te_pos) > 0 else len(row) - 1
            outputs.append(row[start:end + 1].detach().cpu().numpy())
        return outputs

    def generate(self, **kwargs):
        meta: GenerateMeta = kwargs["meta"]
        tokenizer: Tokenizer = kwargs["tokenizer"]
        task: str = kwargs["task"]
        sequence = kwargs["sequence"]

        if task == "MetaGen":
            generated = self._sample_meta_sequences(
                tokenizer,
                sequence,
                p=meta.p,
                temperature=meta.temperature,
            )
            return {
                "meta": meta,
                "task": task,
                "tokenizer": tokenizer,
                "sequence": ([], generated),
            }

        if self.meta["tag"]["model"] == "pretrained":
            _, pack = self.model.top_sampling_measure_kv_cache(
                tokenizer=tokenizer,
                src=sequence,
                p=meta.p,
                temperature=meta.temperature,
            )
            return {
                "meta": meta,
                "task": task,
                "tokenizer": tokenizer,
                "sequence": pack,
            }

        if self.meta["tag"]["model"] == "generation":
            _, pack = self.model.top_sampling_measure_kv_cache(
                tokenizer=tokenizer,
                src=pad_sequence(sequence, batch_first=True, padding_value=tokenizer.get("<PAD>")),
                p=meta.p,
                temperature=meta.temperature,
            )
            return {
                "meta": meta,
                "task": task,
                "tokenizer": tokenizer,
                "sequence": pack,
            }

        raise ValueError(f"Unsupported model tag: {self.meta['tag']['model']}")

    # ------------------------------------------------------------------
    # postprocessing
    # ------------------------------------------------------------------
    def postprocessing(self, save_directory, **kwargs):
        meta: GenerateMeta = kwargs["meta"]
        tokenizer: Tokenizer = kwargs["tokenizer"]
        task: str = kwargs["task"]
        sequence_pack = kwargs["sequence"]

        tokenizer.mode(TO_MUSIC)
        generated_sequences = sequence_pack[1]

        outputs = []
        for i, seq_tokens in enumerate(generated_sequences):
            if task == "MIDI2Chord":
                outputs.append(self._parse_chords(tokenizer, seq_tokens, meta.tempo))
            elif task == "MetaGen":
                outputs.append(self._parse_metadata(tokenizer, seq_tokens))
            else:
                output_path = os.path.join(save_directory, f"output_{i}.mid")
                ct_token_to_midi(tokenizer, seq_tokens, output_path, tempo=meta.tempo)
                outputs.append(output_path)
        return outputs

    # ------------------------------------------------------------------
    # parsers
    # ------------------------------------------------------------------
    def _parse_chords(self, tokenizer: Tokenizer, seq, tempo: int):
        b4 = 60 / tempo
        measure = b4 * 4
        b96 = measure / 96

        measure_start_time = 0.0
        current_time = 0.0
        chords = []

        current_root = None
        current_quality = None
        current_bass = None

        def try_commit():
            nonlocal current_root, current_quality, current_bass
            if current_root is not None:
                qual = current_quality if current_quality and current_quality != "None" else ""
                bass = current_bass if current_bass and current_bass != "None" else ""
                chords.append({"time": round(current_time, 3), "chord": f"{current_root}{qual}{bass}"})
                current_root = None
                current_quality = None
                current_bass = None

        if hasattr(seq, "flatten"):
            seq = seq.flatten()

        for token_id in seq:
            t_id = int(token_id.item()) if hasattr(token_id, "item") else int(token_id)
            token = tokenizer.rev_get(t_id)
            if token is None:
                continue
            if token in {"<TE>", "<EOS>", "<TAG_END>"}:
                break
            if token == "<SME>":
                measure_start_time += measure
            elif token.startswith("s_"):
                try_commit()
                beat = float(token.split("_")[1])
                current_time = measure_start_time + beat * b96
            elif token.startswith("CR_"):
                current_root = token.split("_", 1)[1]
            elif token.startswith("CQ_"):
                current_quality = token.split("_", 1)[1]
            elif token.startswith("CB_"):
                current_bass = token.split("_", 1)[1]

        try_commit()
        return chords

    def _parse_metadata(self, tokenizer: Tokenizer, seq):
        meta_dict = {}
        instruments = []
        densities = {}
        current_inst = None

        if hasattr(seq, "flatten"):
            seq = seq.flatten()

        for token_id in seq:
            t_id = int(token_id.item()) if hasattr(token_id, "item") else int(token_id)
            token = tokenizer.rev_get(t_id)
            if token is None:
                continue
            if token in {"<TE>", "<TAG_END>"}:
                break
            if token in {"<EOS>", "<SYSTEM>", "<META>", "<MGEN>", "<CGEN>"}:
                continue
            if token.startswith("k_"):
                meta_dict["key"] = token[2:]
            elif token.startswith("<INST_"):
                current_inst = token[6:-1]
                instruments.append(current_inst)
            elif token.startswith("<NOTE_DENSE_"):
                density_val = token[12:-1]
                if current_inst is not None:
                    densities[current_inst] = density_val
            elif token.startswith("<GEN_MEASURE_COUNT_"):
                meta_dict["gen_measure_count"] = token[19:-1]

        if instruments:
            meta_dict["instruments"] = instruments
        if densities:
            meta_dict["note_density"] = densities
        return meta_dict