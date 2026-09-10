from __future__ import annotations

from app.config import SafetyConfig


class Whitelist:
    def __init__(self, config: SafetyConfig, owner_qq: str = "") -> None:
        self.config = config
        self.owner_qq = owner_qq

    def is_allowed(self, scene_type: str, scene_id: str, sender_id: str = "") -> bool:
        if scene_type == "group":
            return scene_id in self.config.allow_groups
        if scene_type == "private":
            if self.owner_qq and scene_id == self.owner_qq:
                return True
            return scene_id in self.config.allow_private or sender_id in self.config.allow_private
        return False

