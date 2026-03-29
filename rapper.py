from abc import ABC, abstractmethod
from typing import List, Optional

from pydantic import BaseModel


class GenerateMeta(BaseModel):
    model_type: str
    program: List[str]
    tempo: int
    task: str
    key: str

    num_gems: Optional[int] = 1 # 生成するフレーズの数
    genfield_measure: Optional[int] = 8 # 生成する小節数 (例: 4小節、8小節など)
    gen_note_dense: Optional[dict] = {"PIANO": 4} # 生成されるノートの密度 (8段階で指定、例: 1=まばら、8=非常に密集)
    p: Optional[float] = 0.95 # Top-pサンプリングの確率閾値
    temperature: Optional[float] = 1.0 # 生成の多様性を制御する温度パラメータ
    chord_item: Optional[List[str]] = None # コード進行のアイテム (例: ["Cmaj7", "Dm7", "G7", "Cmaj7"])
    chord_times: Optional[List[float]] = None # コードのタイミング (例: [0.0, 2.0, 4.0, 6.0] - 各コードの開始時間を秒単位で指定)
    split_measure: Optional[int] = 999 # 生成されたシーケンスを分割する小節数 (例: 4小節ごとに分割)
    ai_continue_mode: Optional[bool] = False # AIによって生成された旋律の続きをさらにAIで生成するかどうか (True/False)


# --- Strategy & Factory Pattern ---

class AbstractModelRapper(ABC):
    """
    モデル固有の処理をカプセル化する基底クラス (Strategy)
    モデルの読み込み、前処理、生成、後処理のインターフェースを定義します。
    """
    def __init__(self, model_info):
        self.meta = model_info
        self.model = self._load_model()

    @abstractmethod
    def _load_model(self):
        """モデルのインスタンスを生成し、重みを読み込んで返します。"""
        pass

    @abstractmethod
    def preprocessing(self, past_midi, const_midi, future_midi, meta) -> dict:
        """MIDIやシーケンスをモデルの入力形式に前処理します。"""
        pass

    @abstractmethod
    def generate(self, **kwargs) -> dict:
        """前処理済みデータから新しいシーケンスを生成します。"""
        pass

    @abstractmethod
    def postprocessing(self, save_directory, **kwargs):
        """生成されたデータをMIDIファイルなどの最終形式に後処理します。"""
        pass


class ModelRapperFactory:
    """
    モデルの種類に応じて適切なRapperインスタンスを生成するクラス (Factory)
    """
    def __init__(self):
        self._rappers = {}

    def register_rapper(self, model_type_keyword, rapper_class):
        """
        モデルの種類を示すキーワードとRapperクラスを登録します。
        例: register_rapper("MORTM", MORTM4Rapper)
        """
        self._rappers[model_type_keyword] = rapper_class
        print(f"Registered rapper: {rapper_class.__name__} for type '{model_type_keyword}'")

    def create_rapper(self, model_info):
        model_type = model_info.get('model_name', '')
        for keyword, rapper_class in self._rappers.items():
            if keyword == model_type:
                return rapper_class(model_info)
        raise ValueError(f"適切なモデルラッパーが見つかりません: {model_type}")
