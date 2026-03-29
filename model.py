import json
import os
import asyncio
import zipfile
from typing import Optional, List
from collections import OrderedDict
import gc

# pynvmlのインポートを試みる
try:
    import pynvml
    PYNVML_AVAILABLE = True
except ImportError:
    PYNVML_AVAILABLE = False
    print("Warning: pynvml is not installed. VRAM management will be disabled.")

from rapper import *
from models.mortm.mortm45 import MORTM45Rapper
from models.mortm.mortm46 import MORTM46Rapper
# --- Controller ---
class ModelController:
    def __init__(self, vram_threshold=0.85, cache_size=10):
        global PYNVML_AVAILABLE
        self.rapper_factory = ModelRapperFactory()
        self._register_rappers()

        self.available_models = {}
        self._scan_model_folders()

        self.meta = {i: info for i, info in enumerate(self.available_models.values())}
        print("Initialized. Available models:")
        print(self.meta)

        self.loaded_rappers = OrderedDict()
        self.max_cache_size = cache_size
        self.model_locks = {} # モデルごとのロックを管理

        self.vram_threshold = vram_threshold
        if PYNVML_AVAILABLE:
            try:
                pynvml.nvmlInit()
                self.handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                print("pynvml initialized successfully.")
            except Exception as e:
                print(f"Failed to initialize pynvml: {e}")
                PYNVML_AVAILABLE = False

    def __del__(self):
        if PYNVML_AVAILABLE:
            pynvml.nvmlShutdown()

    def _register_rappers(self):
        self.rapper_factory.register_rapper("MORTM4.5-Flash-Preview", MORTM45Rapper)
        self.rapper_factory.register_rapper("MORTM4.5-Pro-Preview", MORTM45Rapper)
        self.rapper_factory.register_rapper("MORTM4.5-Flash-Preview2", MORTM45Rapper)
        self.rapper_factory.register_rapper("MORTM4.5-Pro-Preview2", MORTM45Rapper)
        self.rapper_factory.register_rapper("MORTM4.5D-Lite", MORTM45Rapper)
        self.rapper_factory.register_rapper("MORTM4.5-Generation-Preview2", MORTM45Rapper)
        self.rapper_factory.register_rapper("MORTM4.5-OMEGA-Preview", MORTM45Rapper)

    def _scan_model_folders(self, base_dir="data/models"):
        if not os.path.isdir(base_dir):
            return
        abs_base_dir = os.path.abspath(base_dir)
        for name in os.listdir(abs_base_dir):
            model_path = os.path.join(abs_base_dir, name)
            if not os.path.isdir(model_path):
                continue
            data_json_path = os.path.join(model_path, "data.json")
            if os.path.exists(data_json_path):
                try:
                    with open(data_json_path, "r", encoding="utf-8") as f:
                        model_info = json.load(f)
                    model_name = model_info.get('model_name')
                    if model_name:
                        model_info['model_folder_path'] = model_path
                        self.available_models[model_name] = model_info
                        print(f"Found model: {model_name}")
                except Exception as e:
                    print(f"Error loading model info from {model_path}: {e}")

    def _unload_model(self, model_name):
        print(f"Unloading model: {model_name}")
        rapper_instance = self.loaded_rappers.pop(model_name, None)
        if rapper_instance:
            del rapper_instance
        if model_name in self.model_locks:
            del self.model_locks[model_name]
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def _check_and_manage_vram(self):
        if not PYNVML_AVAILABLE:
            return
        try:
            mem_info = pynvml.nvmlDeviceGetMemoryInfo(self.handle)
            usage_ratio = mem_info.used / mem_info.total
            print(f"Current VRAM usage: {usage_ratio:.2%}")
            while usage_ratio > self.vram_threshold and self.loaded_rappers:
                model_name, _ = self.loaded_rappers.popitem(last=False)
                print(f"VRAM usage ({usage_ratio:.2%}) exceeds threshold ({self.vram_threshold:.2%}). Unloading LRU model.")
                self._unload_model(model_name)
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(self.handle)
                usage_ratio = mem_info.used / mem_info.total
                print(f"New VRAM usage: {usage_ratio:.2%}")
        except Exception as e:
            print(f"Error during VRAM management: {e}")

    async def generate(self, model_type, past_midi_path: str, const_midi_path: str, future_midi_path: str, meta: GenerateMeta, save_directory):
        if model_type not in self.available_models:
            raise ValueError(f"指定されたモデル({model_type})は存在しません")

        self._check_and_manage_vram()
        if model_type in self.loaded_rappers:
            rapper = self.loaded_rappers[model_type]
            lock = self.model_locks[model_type]
            self.loaded_rappers.move_to_end(model_type)
        else:
            if len(self.loaded_rappers) >= self.max_cache_size:
                lru_model_name, _ = self.loaded_rappers.popitem(last=False)
                self._unload_model(lru_model_name)
            model_info = self.available_models[model_type]
            rapper = self.rapper_factory.create_rapper(model_info)
            lock = asyncio.Lock()
            self.loaded_rappers[model_type] = rapper
            self.model_locks[model_type] = lock

        final_output_path = None
        async with lock:
            kwargs = rapper.preprocessing(past_midi_path, const_midi_path, future_midi_path, meta)
            generated_data_kwargs = rapper.generate(**kwargs)
            output_paths = rapper.postprocessing(save_directory, **generated_data_kwargs)

            if isinstance(output_paths, list):
                if len(output_paths) > 1:
                    zip_path = os.path.join(save_directory, "output.zip")
                    with zipfile.ZipFile(zip_path, 'w') as zf:
                        for file_path in output_paths:
                            zf.write(file_path, os.path.basename(file_path))
                    final_output_path = zip_path
                elif len(output_paths) == 1:
                    final_output_path = output_paths[0]
            else:
                final_output_path = output_paths

        return {
            "result": "success",
            "model_type": model_type,
            "save_path": str(save_directory),
            "output_file": final_output_path
        }
