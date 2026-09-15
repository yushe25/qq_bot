from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from app.config import StickerIngestConfig
from app.domain.types import NormalizedEvent
from app.media.inbound_ingest import IngestResult
from app.memory.sticker_preferences import StickerPreferenceService
from app.storage.db import StateStore

_CONTEXT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("道歉", ("对不起", "抱歉", "道歉", "我的错")),
    ("安慰", ("别难过", "没事的", "抱抱", "加油")),
    ("庆祝", ("庆祝", "生日快乐", "恭喜", "赢了")),
    ("开心", ("哈哈", "笑死", "开心", "好耶", "哈哈哈")),
    ("赞同", ("赞同", "同意", "确实", "可以", "点赞")),
    ("鼓励", ("加油", "干得漂亮", "厉害", "牛")),
    ("疑惑", ("不懂", "没看懂", "什么情况", "？", "?", "离谱")),
    ("生气", ("烦死", "生气", "滚", "别说了")),
    ("吐槽", ("吐槽", "太离谱", "什么鬼", "服了")),
    ("无语", ("无语", "算了", "...", "……")),
    ("认真", ("认真", "严肃", "重要", "注意")),
)


@dataclass
class _PendingObservation:
    observation_id: int
    media_id: int
    group_id: str
    before: list[dict[str, object]]
    after: list[dict[str, object]] = field(default_factory=list)


class StickerContextObserver:
    def __init__(
        self,
        config: StickerIngestConfig,
        store: StateStore,
        preferences: StickerPreferenceService | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.preferences = preferences
        self._recent: dict[str, deque[dict[str, object]]] = {}
        self._pending: dict[str, list[_PendingObservation]] = {}

    async def observe_event(
        self,
        event: NormalizedEvent,
        ingest_results: list[IngestResult] | None = None,
    ) -> None:
        if not self.config.enabled or event.kind != "group":
            return
        group_id = _scene_id(event.session_id)
        group_config = self.config.groups.get(group_id)
        if group_config is None or not group_config.enabled:
            return
        results = [item for item in ingest_results or [] if item.accepted and item.media_id is not None]
        if results:
            before = list(self._recent.get(group_id, deque()))[-self.config.observation_before_messages :]
            for result in results:
                media_id = result.media_id
                if media_id is None:
                    continue
                observation_id = await self.store.insert_sticker_context_observation(
                    media_id=media_id,
                    group_id=group_id,
                    source_message_id=event.event_id,
                    sender_id=event.sender_id,
                    observed_at=event.timestamp,
                    before_context=before,
                    source="group_capture",
                )
                self._pending.setdefault(group_id, []).append(
                    _PendingObservation(
                        observation_id=observation_id,
                        media_id=media_id,
                        group_id=group_id,
                        before=before,
                    )
                )
            return
        text = event.text.replace("\n", " ").strip()
        if not text:
            return
        summary = {
            "sender_id": event.sender_id,
            "sender_name": event.sender_name,
            "text": text[:160],
            "timestamp": event.timestamp,
        }
        recent = self._recent.setdefault(group_id, deque(maxlen=max(1, self.config.observation_before_messages)))
        recent.append(summary)
        await self._fill_after(group_id, self.config.observation_after_messages, summary)

    async def _fill_after(self, group_id: str, max_after: int, summary: dict[str, object]) -> None:
        pending = self._pending.get(group_id, [])
        keep: list[_PendingObservation] = []
        for observation in pending:
            observation.after.append(summary)
            if len(observation.after) >= max_after:
                tags = _classify_situation([*observation.before, *observation.after])
                await self.store.update_sticker_context_after(
                    observation.observation_id,
                    observation.after,
                    tags,
                )
                if self.preferences is not None and tags:
                    await self.preferences.record_member_usage(
                        observation.media_id,
                        observation.group_id,
                        tags,
                    )
            else:
                keep.append(observation)
        if keep:
            self._pending[group_id] = keep
        else:
            self._pending.pop(group_id, None)


def _classify_situation(context: list[dict[str, object]]) -> list[str]:
    text = " ".join(str(item.get("text", "")) for item in context)
    labels = [label for label, keywords in _CONTEXT_RULES if any(keyword in text for keyword in keywords)]
    return labels[:5]


def _scene_id(session_id: str) -> str:
    return session_id.split(":", 1)[1] if ":" in session_id else session_id
