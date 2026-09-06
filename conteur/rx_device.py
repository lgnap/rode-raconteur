"""Repérage du récepteur. L'objet PyAudio est injecté."""

from dataclasses import dataclass

NEEDLE = "wireless pro rx"


@dataclass(frozen=True)
class RxDevice:
    index: int
    name: str


def find_rx(pa) -> RxDevice | None:
    """Première entrée dont le nom désigne le RX et qui accepte 2 canaux."""
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if NEEDLE not in str(info["name"]).lower():
            continue
        if int(info["maxInputChannels"]) < 2:
            continue
        return RxDevice(index=i, name=str(info["name"]))
    return None
