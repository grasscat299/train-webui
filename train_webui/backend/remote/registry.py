"""マシン名 → RemoteController の対応（複数の学習用PCを1つの server から扱う）。"""
from __future__ import annotations

from typing import Callable

from ..settings import Settings
from .types import RemoteControllerProtocol


class RemoteRegistry:
    def __init__(self, controllers: dict[str, RemoteControllerProtocol], default: str) -> None:
        if default not in controllers:
            raise KeyError(f"既定のマシン {default} が登録されていません")
        self._controllers = dict(controllers)
        self.default_name = default

    @classmethod
    def from_settings(cls, settings: Settings,
                      factory: Callable[[Settings], RemoteControllerProtocol] | None = None) -> RemoteRegistry:
        if factory is None:
            from .controller import RemoteController
            factory = RemoteController
        return cls({name: factory(settings.machine_settings(name)) for name in settings.machines},
                   settings.default_machine)

    @classmethod
    def single(cls, controller: RemoteControllerProtocol, name: str = "default") -> RemoteRegistry:
        """1台だけの registry（テストの偽 remote 用）。"""
        return cls({name: controller}, name)

    @property
    def names(self) -> list[str]:
        return list(self._controllers)

    def get(self, name: str) -> RemoteControllerProtocol:
        try:
            return self._controllers[name]
        except KeyError:
            raise KeyError(f"未登録のマシン: {name}（登録済み: {', '.join(self._controllers)}）") from None
