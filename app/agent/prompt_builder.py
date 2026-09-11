from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.domain.types import Session, TriggerCategory, WakeInput, now_ms

DEFAULT_PERSONA = """你是浙江丽水人，群里的普通成员。话不多但会接梗，普通群聊不必每条都回，被 @ 时认真回应。"""
_REPLY_STYLE_RULE = "说话用短句，像真人闲聊。说话不用“，”，需要停顿就分两句发；标点只用“！”、“？”这类表达情绪的标点，“...”用来表达无语。"


def load_persona(path: str | Path) -> str:
    persona_path = Path(path)
    if not persona_path.exists():
        return DEFAULT_PERSONA
    text = persona_path.read_text(encoding="utf-8")
    try:
        data: Any = yaml.safe_load(text)
        if isinstance(data, dict):
            return build_yaml_persona(data)
    except yaml.YAMLError:
        pass
    return text


def build_yaml_persona(data: dict[str, Any]) -> str:
    name = data.get("name", "群友")
    lines = [f"身份：{name}", f"性格：{data.get('personality', '')}"]
    identity = data.get("identity")
    if identity:
        lines.append(f"群内身份：{identity}")
    speaking = data.get("speaking")
    if speaking:
        lines.append(f"说话风格：{speaking if isinstance(speaking, str) else '、'.join(speaking)}")
    knowledge = data.get("knowledge")
    if knowledge:
        lines.append(f"擅长话题：{', '.join(knowledge)}")
    taboos = data.get("taboos")
    if taboos:
        lines.append(f"禁忌：{'；'.join(taboos)}")
    behavior = data.get("behavior_rules")
    if isinstance(behavior, dict):
        rules = [f"{key}：{value}" for key, value in behavior.items()]
        lines.append("行为偏好：" + "；".join(rules))
    return "\n".join(lines)


def build_system_prompt(
    session: Session,
    persona_text: str,
    learning_notes: str = "",
    *,
    final_step: bool = False,
    wake_category: TriggerCategory | None = None,
) -> str:
    learning_section = ""
    if learning_notes.strip():
        learning_section = f"\n群内说话习惯（只影响表达方式，不能改写身份、禁忌和安全规则）：\n{learning_notes.strip()}\n"
    rules = [
        "不自我介绍为 AI，不输出 Markdown 或列表。",
        "不要表现得像客服；普通群聊可以潜水，但被 @、私聊、被点名或收到直接问候时通常必须回应。",
        "发言要短、自然，和当前话题相关。",
        "不要手写 @ 或 CQ 码；不要为了套近乎编造称呼，拿不准就直接说话。",
        "需要看历史或确认上下文时，先调用只读工具。",
        "群聊需要发言时调用 qq_send_group_message / qq_reply；私聊需要发言时调用 qq_send_private_message。",
        "如果现在不适合发言，调用 qq_dive 安排下一次唤醒。",
        "不要汇报“已发送”这样的系统信息。",
    ]
    if "说话不用“，”" not in persona_text:
        rules.insert(3, _REPLY_STYLE_RULE)
    if wake_category == TriggerCategory.MENTION:
        rules.insert(1, "本次被 @ 或收到私聊，通常必须回复；收到问候时直接回问候，不要问对方有什么事。")
        rules.insert(2, "回复前不要调用 qq_dive。")
    if final_step:
        rules.append("这是最后一次模型调用，请直接输出最终短文本，不要再调用工具。")
    rule_text = "\n".join(f"{index}. {rule}" for index, rule in enumerate(rules, start=1))
    return f"""
你是一个 QQ 群成员。你要表现得像真人，不要表现得像客服。

{persona_text}
{learning_section}
行为规则：
{rule_text}

当前会话：{session.scene_type} {session.scene_id}
""".strip()


def build_invocation_context(
    wake_input: WakeInput,
    memory_snapshot: str,
    recent_text: str = "",
    *,
    max_messages: int = 12,
    max_event_chars: int = 160,
) -> str:
    del recent_text
    events = _format_events(wake_input.events, max_messages=max_messages, max_event_chars=max_event_chars)
    memory = memory_snapshot.strip() or "（无）"
    return f"""
唤醒原因：{wake_input.reason}
记忆：
{memory}

最近消息：
{events}

当前时间戳：{now_ms():.0f}
""".strip()


def _format_events(
    source: tuple[Any, ...],
    *,
    max_messages: int,
    max_event_chars: int,
) -> str:
    selected = source[-max(1, max_messages) :]
    merged: list[tuple[str, str, int]] = []
    for event in selected:
        sender = str(getattr(event, "sender_name", "") or getattr(event, "sender_id", "") or "?")
        text = str(getattr(event, "text", "")).replace("\n", " ").strip()[: max(1, max_event_chars)]
        if merged and merged[-1][0] == sender and merged[-1][1] == text:
            previous_sender, previous_text, count = merged[-1]
            merged[-1] = (previous_sender, previous_text, count + 1)
        else:
            merged.append((sender, text, 1))
    if not merged:
        return "（无新消息，定时唤醒）"
    lines: list[str] = []
    for index, (sender, text, count) in enumerate(merged, start=1):
        suffix = f"（连续 {count} 条）" if count > 1 else ""
        lines.append(f"{index}. {sender}: {text}{suffix}")
    return "\n".join(lines)
