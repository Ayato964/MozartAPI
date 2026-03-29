import json
import os

import numpy as np
import torch

from pretty_midi import PrettyMIDI
from torch import Tensor
from torch.nn.utils.rnn import pad_sequence
from rapper import *

from mortm.models.mortm import MORTM, MORTMArgs
from mortm.models.modules.progress import _DefaultLearningProgress
from mortm.utils.generate import *
from mortm.utils.convert import MIDI2Seq
from mortm.train.tokenizer import *
from model import AbstractModelRapper


def make_system_prompt(tokenizer, key, program, call_function = None):
    prompt = [tokenizer.get("<EOS>"),
              tokenizer.get("<SYSTEM>")]

    for p in program:
        prompt.append(tokenizer.get(f"<INST_{p}>"))
    if call_function is not None:
        call_function(prompt)
    prompt.append(tokenizer.get(f"k_{key}"))
    prompt.append(tokenizer.get("<TAG_END>"))
    return np.array(prompt)

class MORTM46Rapper(AbstractModelRapper):

    """MORTMモデル用の具体的な処理を実装したクラス (Concrete Strategy)"""
    def _load_model(self):
        model_path = self.meta['model_folder_path']
        config_path = os.path.join(model_path, "config.json")
        model_pth_path = os.path.join(model_path, "model.pth")

        print(f"Loading model: {self.meta['model_name']} from {model_path}!!!!!")
        args = MORTMArgs(config_path)
        progress = _DefaultLearningProgress()
        model: MORTM = MORTM(args, progress).to(progress.get_device())
        model.load_state_dict(torch.load(model_pth_path), strict=False)
        model.eval() # 推論モードに設定
        return model

    def preprocessing(self, past_midi, const_midi, future_midi, meta: GenerateMeta):

        if self.meta["tag"]["model"] == "pretrained":
            tokenizer = Tokenizer(get_token_converter_pro2(TO_TOKEN))
            seq = []
            print(meta.num_gems)
            for _ in range(meta.num_gems):
                prompt = make_system_prompt(tokenizer, meta.key, meta.program)
                prompt = np.concatenate([prompt, np.array([tokenizer.get("<MGEN>")])])
                seq.append(prompt)
            return {"meta": meta, "sequence": np.array(seq), "tokenizer": tokenizer}
        elif self.meta["tag"]["model"] == "generation":
            def call(p: list):
                p.append(tokenizer.get(f"<GEN_MEASURE_COUNT_{min(meta.genfield_measure, 8)}>"))

            tokenizer = Tokenizer(omega_converter(TO_TOKEN))
            seq = []
            for _ in range(meta.num_gems):
                prompt = make_system_prompt(tokenizer, meta.key, meta.program, call_function=call)
                if past_midi is not None:
                    print("----PAST MIDI LOAD----")
                    past = MIDIConverter(tokenizer, os.path.dirname(past_midi), os.path.basename(past_midi), program_list=meta.program, key=meta.key)
                    past()
                    node_dict = past.midi2seq.aya_node
                    past_seq = self.get_context(tokenizer, node_dict, "<PAST_M>")
                    prompt = np.concatenate([prompt, np.array(past_seq)])

                if meta.chord_item is not None:
                    print("----CHORD LOAD----")
                    chord = MetaData2Chord(tokenizer, meta.key, meta.chord_item, meta.chord_times, meta.tempo, None, None, 999, False)
                    chord()
                    c = [tokenizer.get("<CONST_C>")]
                    c.append(tokenizer.get(f"<INST_SAX>"))
                    c.extend(chord.aya_node[1])
                    c.append(tokenizer.get("<ESEQ>"))
                    c.append(tokenizer.get("<TAG_END>"))

                    prompt = np.concatenate([prompt, np.array(c)])

                if const_midi is not None:
                    print("----CONST MIDI LOAD----")
                    const = MIDIConverter(tokenizer, os.path.dirname(const_midi), os.path.basename(const_midi), program_list=meta.program, key=meta.key)
                    const()
                    node_dict = const.midi2seq.aya_node
                    const_seq = self.get_context(tokenizer, node_dict, "<CONST_M>")
                    prompt = np.concatenate([prompt, np.array(const_seq)])

                if future_midi is not None:
                    print("----FUTURE MIDI LOAD----")
                    future = MIDIConverter(tokenizer, os.path.dirname(future_midi), os.path.basename(future_midi), program_list=meta.program, key=meta.key)
                    future()
                    node_dict = future.midi2seq.aya_node
                    future_seq = self.get_context(tokenizer, node_dict, "<FUTURE_M>")
                    prompt = np.concatenate([prompt, np.array(future_seq)])

                prompt = np.concatenate([prompt, np.array([tokenizer.get("<MGEN>")])])
                seq.append(torch.tensor(prompt).to('cuda'))

            return {"meta": meta, "sequence": seq, "tokenizer": tokenizer}

    def get_context(self, tokenizer: Tokenizer, node_dict: dict, key_token: str):
        past_seq = [tokenizer.get(key_token)]
        for program, inst in node_dict.items():
            past_seq.append(tokenizer.get(f"<INST_{program}>"))
            past_seq.extend(inst[:-1])
            past_seq.append(tokenizer.get(f"<ESEQ>"))
        past_seq.append(tokenizer.get("<TAG_END>"))
        return past_seq


    def generate(self, **kwargs):
        meta: GenerateMeta = kwargs['meta']
        if self.meta["tag"]["model"] == "pretrained":
            np_all, pack = self.model.top_sampling_measure_kv_cache(tokenizer=kwargs["tokenizer"], src=kwargs["sequence"],
                                                                    p=meta.p, temperature=meta.temperature)
            return {"meta": kwargs['meta'], "tokenizer": kwargs['tokenizer'], "sequence": pack}

        if self.meta["tag"]["model"] == "generation":
            np_all, pack = self.model.top_sampling_measure_kv_cache(tokenizer=kwargs["tokenizer"], src=pad_sequence(kwargs["sequence"], batch_first=True),
                                                                    p=meta.p, temperature=meta.temperature)

            return {"meta": kwargs['meta'], "tokenizer": kwargs['tokenizer'], "sequence": pack}

    def postprocessing(self, save_directory, **kwargs):

        if self.meta["tag"]["model"] == "pretrained" or self.meta["tag"]["model"] == "generation":
            meta: GenerateMeta = kwargs['meta']
            tokenizer: Tokenizer = kwargs['tokenizer']
            tokenizer.mode(TO_MUSIC)
            sequence = kwargs['sequence']
            outputs = []
            for i in range(len(sequence[1])):
                output_path = os.path.join(save_directory, f"output_{i}.mid")
                outputs.append(output_path)
                ct_token_to_midi(tokenizer, sequence[1][i], output_path, tempo=meta.tempo)
            return outputs