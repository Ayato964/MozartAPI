import json
import os

import numpy as np
import torch

from pretty_midi import PrettyMIDI
from torch import Tensor

from rapper import *

from mortm.models.mortm import MORTM, MORTMArgs
from mortm.models.modules.progress import _DefaultLearningProgress
from mortm.utils.generate import *
from mortm.utils.convert import MIDI2Seq
from mortm.train.tokenizer import *
from model import AbstractModelRapper


def make_system_prompt(tokenizer, key, program):
    prompt = [tokenizer.get("<EOS>"),
              tokenizer.get("<SYSTEM>")]

    for p in program:
        prompt.append(tokenizer.get(f"<INST_{p}>"))
    prompt.append(tokenizer.get(f"k_{key}"))
    prompt.append(tokenizer.get("<TAG_END>"))
    prompt.append(tokenizer.get("<MGEN>"))
    return np.array(prompt)

class MORTM45Rapper(AbstractModelRapper):

    """MORTMモデル用の具体的な処理を実装したクラス (Concrete Strategy)"""
    def _load_model(self):
        model_path = self.meta['model_folder_path']
        config_path = os.path.join(model_path, "config.json")
        model_pth_path = os.path.join(model_path, "model.pth")

        print(f"Loading model: {self.meta['model_name']} from {model_path}")
        args = MORTMArgs(config_path)
        progress = _DefaultLearningProgress()
        model: MORTM = MORTM(args, progress).to(progress.get_device())
        model.load_state_dict(torch.load(model_pth_path), strict=False)
        model.eval() # 推論モードに設定
        return model

    def preprocessing(self, midi_path, meta: GenerateMeta):

        if self.meta["tag"]["model"] == "pretrained":
            tokenizer = Tokenizer(get_token_converter_pro(TO_TOKEN))
            seq = []
            for _ in range(meta.num_gems):
                seq.append(make_system_prompt(tokenizer, meta.key, meta.program))
            return {"meta": meta, "sequence": np.array(seq), "tokenizer": tokenizer}


    def generate(self, **kwargs):
        meta: GenerateMeta = kwargs['meta']
        if self.meta["tag"]["model"] == "pretrained":
            np_all, pack = self.model.top_sampling_measure_kv_cache(tokenizer=kwargs["tokenizer"], src=kwargs["sequence"],
                                                     p=meta.p, temperature=meta.temperature)
            return {"meta": kwargs['meta'], "tokenizer": kwargs['tokenizer'], "sequence": pack}

    def postprocessing(self, save_directory, **kwargs):

        if self.meta["tag"]["model"] == "pretrained":
            meta: GenerateMeta = kwargs['meta']
            tokenizer: Tokenizer = kwargs['tokenizer']
            sequence: Tensor = kwargs['sequence']
            output_path = os.path.join(save_directory, f"output.mid")
            m = ct_token_to_midi(tokenizer, sequence, output_path, tempo=meta.tempo)
            return output_path