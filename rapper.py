from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, Field, model_validator


class GenerateMeta(BaseModel):
    model_type: str
    program: List[Union[str, int]]
    tempo: int
    task: str = "Prompt2MIDI"
    key: Optional[str] = None

    num_gems: int = 1
    genfield_measure: int = 8
    gen_note_dense: Union[int, Dict[str, int]] = Field(default_factory=lambda: {"PIANO": 4})
    p: float = 0.95
    temperature: float = 1.0
    chord_item: Optional[List[str]] = None
    chord_times: Optional[List[float]] = None
    split_measure: int = 999
    ai_continue_mode: bool = False

    @model_validator(mode="after")
    def validate_values(self):
        if self.tempo <= 0:
            raise ValueError("tempo must be > 0")
        if self.num_gems <= 0:
            raise ValueError("num_gems must be >= 1")
        if self.genfield_measure <= 0:
            raise ValueError("genfield_measure must be >= 1")
        if not (0.0 < self.p <= 1.0):
            raise ValueError("p must be in (0, 1]")
        if self.temperature <= 0.0:
            raise ValueError("temperature must be > 0")
        if (self.chord_item is None) ^ (self.chord_times is None):
            raise ValueError("chord_item と chord_times は両方指定するか、両方省略してください")
        if self.chord_item is not None and len(self.chord_item) != len(self.chord_times):
            raise ValueError("chord_item と chord_times の長さが一致していません")
        return self


class AbstractModelRapper(ABC):
    def __init__(self, model_info):
        self.meta = model_info
        self.model = self._load_model()

    @abstractmethod
    def _load_model(self):
        pass

    @abstractmethod
    def preprocessing(self, past_midi, const_midi, future_midi, meta) -> dict:
        pass

    @abstractmethod
    def generate(self, **kwargs) -> dict:
        pass

    @abstractmethod
    def postprocessing(self, save_directory, **kwargs):
        pass


class ModelRapperFactory:
    def __init__(self):
        self._rappers = {}

    def register_rapper(self, model_type_keyword, rapper_class):
        self._rappers[model_type_keyword] = rapper_class
        print(f"Registered rapper: {rapper_class.__name__} for type '{model_type_keyword}'")

    def create_rapper(self, model_info):
        model_type = model_info.get("model_name", "")
        for keyword, rapper_class in self._rappers.items():
            if keyword == model_type:
                return rapper_class(model_info)
        raise ValueError(f"適切なモデルラッパーが見つかりません: {model_type}")