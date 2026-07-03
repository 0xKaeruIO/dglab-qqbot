# -*- coding: utf-8 -*-
import os

import astrbot.api.message_components as Comp
import yaml
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star

from dglab_core import ReplyContext, init_user_manager, user_manager


def load_user_id_map(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("user_id_map", {})
    return {}


class AstrBotReply(ReplyContext):
    def __init__(self, event: AstrMessageEvent, qr_image_url: str | None = None):
        self.event = event
        self.qr_image_url = qr_image_url

    async def send_text(self, text: str) -> None:
        await self.event.send(self.event.plain_result(text))

    async def send_image_file(self, path: str) -> None:
        if self.qr_image_url:
            await self.send_image_url(self.qr_image_url)
            return
        await self.event.send(
            self.event.chain_result([Comp.Image.fromFileSystem(path)])
        )

    async def send_image_url(self, url: str) -> None:
        await self.event.send(self.event.chain_result([Comp.Image.fromURL(url)]))


class DGLabPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.user_id_map_path = os.path.join(self.plugin_dir, "data", "user_id_map.yaml")
        legacy_map = os.path.join(self.plugin_dir, "user_id_map.yaml")
        if not os.path.exists(self.user_id_map_path) and os.path.exists(legacy_map):
            self.user_id_map_path = legacy_map
        self.user_id_map = load_user_id_map(self.user_id_map_path)
        init_user_manager(int(self.config.get("base_port", 5678)))
        logger.info("DGLAB 插件已加载，命令前缀: %s", self._get_prefix())

    def _get_prefix(self) -> str:
        return str(self.config.get("command_prefix", "dgkab")).strip()

    def _get_ip(self) -> str:
        return str(self.config.get("ip_addr", "ws://127.0.0.1")).rstrip("/")

    def _parse_command_body(self, message_str: str) -> str | None:
        text = message_str.strip()
        if text.startswith("/"):
            text = text[1:].lstrip()
        prefix = self._get_prefix()
        if not prefix:
            return None
        if not text.lower().startswith(prefix.lower()):
            return None
        body = text[len(prefix) :].lstrip()
        return body if body else None

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=5)
    async def on_group_message(self, event: AstrMessageEvent):
        """监听群消息，匹配自定义前缀后执行 DGLAB 命令。"""
        body = self._parse_command_body(event.message_str)
        if body is None:
            return

        event.stop_event()

        qq_id = str(event.get_sender_id())
        user_name = event.get_sender_name() or qq_id
        qr_url = str(self.config.get("qr_image_url", "")).strip() or None
        reply = AstrBotReply(event, qr_image_url=qr_url)

        conn = user_manager.ensure_commander(qq_id, user_name, self._get_ip(), reply)
        commander = conn["commander"]
        commander._help_prefix = self._get_prefix()

        logger.info("用户 %s 执行 DGLAB 命令: %s", qq_id, body.split()[0] if body else "")
        await commander.resolve(body, self.user_id_map, self.user_id_map_path)
        self.user_id_map = load_user_id_map(self.user_id_map_path)

    async def terminate(self):
        logger.info("DGLAB 插件已卸载")
