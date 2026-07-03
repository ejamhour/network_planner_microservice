from dataclasses import dataclass
from typing import Any


@dataclass(init=False)
class NodeConfig:
    name: str
    pos: list[float]
    ant_type: str
    ant_gain: float
    ant_height: float
    extra: dict[str, Any]

    def __init__(
        self,
        name: str,
        pos: list[float],
        ant_type: str = "OMNI",
        ant_gain: float = 0.0,
        ant_height: float = 7.0,
        **extra: Any,
    ):
        self.name = name
        self.pos = pos
        self.ant_type = ant_type
        self.ant_gain = ant_gain
        self.ant_height = ant_height
        self.extra = extra

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "pos": self.pos,
            "ant_type": self.ant_type,
            "ant_gain": self.ant_gain,
            "ant_height": self.ant_height,
            **self.extra,
        }


@dataclass(init=False)
class TxConfig(NodeConfig):
    pw: float

    def __init__(self, pw: float = 30.0, **kwargs: Any):
        super().__init__(**kwargs)
        self.pw = pw

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "pw": self.pw,
        }


@dataclass(init=False)
class RxConfig(NodeConfig):
    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)


@dataclass(init=False)
class LinkConfig:
    tx: TxConfig
    rx: RxConfig
    extra: dict[str, Any]

    def __init__(self, tx: TxConfig, rx: RxConfig, **extra: Any):
        self.tx = tx
        self.rx = rx
        self.extra = extra

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LinkConfig":
        link_extra = {
            k: v for k, v in data.items()
            if k not in {"tx", "rx"}
        }

        return cls(
            tx=TxConfig(**data["tx"]),
            rx=RxConfig(**data["rx"]),
            **link_extra,
        )
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "tx": self.tx.to_dict(),
            "rx": self.rx.to_dict(),
            **self.extra,
        }