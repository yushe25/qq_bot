from __future__ import annotations


def build_tool_schemas(*, allow_tools: bool = True) -> list[dict[str, object]]:
    if not allow_tools:
        return []
    return [
        {
            "type": "function",
            "function": {
                "name": "qq_get_group_history",
                "description": "读取当前群的最近消息历史，只读。",
                "parameters": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "qq_get_members",
                "description": "查看当前群成员摘要，只读。",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "qq_send_group_message",
                "description": "向当前群发送一条纯文本消息。groupId 必须是当前会话群号。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "groupId": {"type": "string"},
                        "message": {"type": "string", "maxLength": 500},
                    },
                    "required": ["groupId", "message"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "qq_reply",
                "description": "引用当前会话中的一条消息并回复，目标消息必须属于当前会话。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "maxLength": 500},
                        "targetMessageId": {"type": "string"},
                    },
                    "required": ["message", "targetMessageId"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "qq_send_private_message",
                "description": "向当前私聊会话或 owner 发送一条纯文本私聊消息。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "userId": {"type": "string"},
                        "message": {"type": "string", "maxLength": 500},
                    },
                    "required": ["userId", "message"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "qq_wait",
                "description": "等待若干毫秒后继续思考，单次最大 120000。",
                "parameters": {
                    "type": "object",
                    "properties": {"durationMs": {"type": "integer", "minimum": 1, "maximum": 120000}},
                    "required": ["durationMs"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "qq_dive",
                "description": "潜水并安排下一次唤醒时间，单位毫秒。",
                "parameters": {
                    "type": "object",
                    "properties": {"nextWakeMs": {"type": "integer", "minimum": 1000}, "reason": {"type": "string"}},
                    "required": ["nextWakeMs", "reason"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "memory_update",
                "description": "更新当前会话的轻量记忆，kind 为 active_topic、pending_thought 或 impression。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["active_topic", "pending_thought", "impression"]},
                        "key": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["kind", "key", "value"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "sticker_send",
                "description": "从白名单表情库发送表情包。",
                "parameters": {
                    "type": "object",
                    "properties": {"stickerId": {"type": "string"}},
                    "required": ["stickerId"],
                },
            },
        },
    ]
