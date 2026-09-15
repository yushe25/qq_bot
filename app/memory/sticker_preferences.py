from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from app.storage.db import StateStore


class StickerPreferenceService:
    def __init__(self, store: StateStore) -> None:
        self.store = store

    async def record_member_usage(self, media_id: int, group_id: str, tags: Sequence[str]) -> None:
        for tag in tags:
            await self.store.increment_sticker_preference(
                media_id,
                group_id,
                tag,
                positive_delta=1.0,
                usage_delta=1,
            )

    async def apply_owner_correction(
        self,
        media_id: int,
        group_id: str,
        tags: Sequence[str],
        *,
        suitable: bool,
    ) -> None:
        for tag in tags:
            await self.store.increment_sticker_preference(
                media_id,
                group_id,
                tag,
                positive_delta=1.5 if suitable else 0.0,
                negative_delta=0.0 if suitable else 2.0,
            )

    async def experience_for_group(
        self,
        group_id: str,
        media_to_sticker: dict[int, str],
    ) -> dict[str, str]:
        if not media_to_sticker:
            return {}
        rows = await self.store.list_sticker_preferences(group_id, list(media_to_sticker))
        grouped: dict[int, list[tuple[str, float, float]]] = defaultdict(list)
        for row in rows:
            grouped[int(row["media_id"])].append(
                (str(row["situation_tag"]), float(row["positive_score"]), float(row["negative_score"]))
            )
        result: dict[str, str] = {}
        for media_id, sticker_id in media_to_sticker.items():
            values = grouped.get(media_id, [])
            positive = [f"{tag} +{score:g}" for tag, score, _ in values if score > 0]
            negative = [f"不适合{tag}" for tag, _, score in values if score > 0]
            parts = [*positive[:3], *negative[:2]]
            if parts:
                result[sticker_id] = "；".join(parts)
        return result
