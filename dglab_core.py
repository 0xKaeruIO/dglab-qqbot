# -*- coding: utf-8 -*-
import asyncio
import logging
import os
import random

import qrcode
import yaml
from pydglab_ws import Channel, DGLabWSServer, RetCode, StrengthData, StrengthOperationType

from Pulses import PULSE_DATA

_log = logging.getLogger(__name__)


class ReplyContext:
    """AstrBot 消息回复抽象，与具体平台解耦。"""

    async def send_text(self, text: str) -> None:
        raise NotImplementedError

    async def send_image_file(self, path: str) -> None:
        raise NotImplementedError

    async def send_image_url(self, url: str) -> None:
        raise NotImplementedError


class UserConnectionManager:
    def __init__(self, base_port: int = 5678):
        self.user_connections = {}
        self.port_counter = base_port

    def get_user_connection(self, qq_id: str, user_name: str | None = None):
        if qq_id not in self.user_connections:
            self.user_connections[qq_id] = {
                "commander": None,
                "port": self.port_counter,
                "status": "disconnected",
                "user_name": user_name or qq_id,
            }
            self.port_counter += 1
        else:
            if user_name and not self.user_connections[qq_id].get("user_name"):
                self.user_connections[qq_id]["user_name"] = user_name
        return self.user_connections[qq_id]

    def ensure_commander(self, qq_id: str, user_name: str, ip: str, reply: ReplyContext):
        conn = self.get_user_connection(qq_id, user_name)
        if conn["commander"] is None:
            conn["commander"] = Commander(qq_id, ip, reply)
        else:
            conn["commander"].reply = reply
        return conn

    def get_all_users(self):
        return list(self.user_connections.keys())


def make_qrcode(data: str, output_dir: str, qq_id: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    filename = os.path.join(output_dir, f"qrcode_{qq_id}.png")
    qrcode.make(data).save(filename)
    return filename


class Commander:
    def __init__(self, qq_id: str, ip: str, reply: ReplyContext):
        self.qq_id = qq_id
        self.ip = ip
        self.reply = reply
        self.close_tag = False
        self.pulse_close_tag = False
        self.qr_image_path: str | None = None
        self.size = 0
        self.kwargs: list[str] = []
        self.command = ""
        self.client = None
        self.sever = None
        self.strength = None
        self.status_code = 0
        self.current_pulses_A = PULSE_DATA["呼吸"]
        self.current_pulses_A_name = "呼吸"
        self.current_pulses_B = PULSE_DATA["呼吸"]
        self.current_pulses_B_name = "呼吸"
        self.port = None

    async def send_message(self, message: str):
        await self.reply.send_text(message)

    async def send_qr_image(self, path: str, url: str | None = None):
        self.qr_image_path = path
        if url:
            await self.reply.send_image_url(url)
        else:
            await self.reply.send_image_file(path)

    async def resolve(self, content: str, user_id_map: dict, user_id_map_path: str):
        parts = content.split()
        if not parts:
            return

        self.command = parts[0]
        if self.command.startswith("/"):
            self.command = self.command[1:]
        self.kwargs = parts[1:]
        self.size = len(parts)

        handlers = {
            "增加强度": self.increase,
            "降低强度": self.decrease,
            "断开连接": self.close,
            "新建连接": self.connect,
            "设置强度": self.set,
            "当前状态": self.status,
            "改变波形": self.change_pulse,
            "帮助": self.help,
            "用户列表": self.user_list,
            "设置名称": self.setid2username,
            "随机增加": self.random_increase,
            "随机降低": self.random_decrease,
            "全体随机增加": self.random_increase_all,
            "全体随机降低": self.random_decrease_all,
        }
        handler = handlers.get(self.command)
        if handler:
            await handler(user_id_map, user_id_map_path)
        else:
            await self.send_message("此命令不存在")
            _log.warning("用户 %s 未知命令: %s", self.qq_id, self.command)

    async def connect(self, user_id_map: dict, user_id_map_path: str):
        if self.size >= 2:
            await self.send_message("连接命令不应有参数")
            return

        if self.status_code == 1:
            if self.qr_image_path and os.path.exists(self.qr_image_path):
                await self.send_qr_image(self.qr_image_path)
            _log.info("用户 %s 已重复发送二维码", self.qq_id)
            return
        if self.status_code == 2:
            await self.send_message("当前已连接 app，不可重复连接")
            return

        user_conn = _require_user_manager().get_user_connection(self.qq_id)
        self.port = user_conn["port"]
        user_ip_addr = f"{self.ip}:{self.port}"

        async with DGLabWSServer("0.0.0.0", self.port, 20) as self.sever:
            self.client = self.sever.new_local_client()
            qr_data = self.client.get_qrcode(user_ip_addr)
            _log.info(
                "用户 %s 已创建 DGLabWSServer，二维码 %s",
                user_id_map.get(self.qq_id, self.qq_id),
                qr_data,
            )

            qr_dir = os.path.join(os.path.dirname(__file__), "data", "qrcodes")
            qr_filename = make_qrcode(qr_data, qr_dir, self.qq_id)
            await self.send_qr_image(qr_filename)

            self.status_code = 1
            user_conn["status"] = "connecting"
            await self.client.bind()
            self.status_code = 2
            user_conn["status"] = "connected"
            _log.info("用户 %s 已与 App %s 成功绑定", self.qq_id, self.client.target_id)

            async for data in self.client.data_generator():
                if self.close_tag:
                    self.close_tag = False
                    self.status_code = 0
                    user_conn["status"] = "disconnected"
                    _log.info("用户 %s 已主动断开连接", user_id_map.get(self.qq_id, self.qq_id))
                    return

                asyncio.create_task(self.__send_pulse())

                if isinstance(data, StrengthData):
                    _log.info(
                        "用户 %s 收到强度更新: %s",
                        user_id_map.get(self.qq_id, self.qq_id),
                        data,
                    )
                    self.strength = data
                elif data == RetCode.CLIENT_DISCONNECTED:
                    self.status_code = 0
                    self.pulse_close_tag = True
                    user_conn["status"] = "disconnected"
                    _log.info("用户 %s App 端断开连接", user_id_map.get(self.qq_id, self.qq_id))
                    return

    async def __send_pulse(self):
        while True:
            if self.pulse_close_tag:
                self.pulse_close_tag = False
                return
            if self.client:
                await self.client.add_pulses(Channel.A, *self.current_pulses_A * 3)
                await self.client.add_pulses(Channel.B, *self.current_pulses_B * 3)
            await asyncio.sleep(1)

    async def change_pulse_for_all(self, user_id_map: dict, *args):
        mgr = _require_user_manager()
        affected = 0
        for user_id in mgr.get_all_users():
            user_conn = mgr.get_user_connection(user_id)
            commander = user_conn["commander"]
            if commander and commander.status_code == 2 and commander.client:
                try:
                    if len(args) == 1:
                        commander.current_pulses_A = PULSE_DATA[args[0]]
                        commander.current_pulses_A_name = args[0]
                        commander.current_pulses_B = PULSE_DATA[args[0]]
                        commander.current_pulses_B_name = args[0]
                        await commander.client.clear_pulses(Channel.A)
                        await commander.client.clear_pulses(Channel.B)
                    elif len(args) == 2:
                        if args[0] == "A":
                            commander.current_pulses_A = PULSE_DATA[args[1]]
                            commander.current_pulses_A_name = args[1]
                            await commander.client.clear_pulses(Channel.A)
                        elif args[0] == "B":
                            commander.current_pulses_B = PULSE_DATA[args[1]]
                            commander.current_pulses_B_name = args[1]
                            await commander.client.clear_pulses(Channel.B)
                    affected += 1
                except Exception as e:
                    _log.error("更改用户 %s 波形失败: %s", user_id_map.get(user_id, user_id), e)
        return affected

    async def change_pulse(self, user_id_map: dict, user_id_map_path: str):
        param_kwargs = self.kwargs.copy()
        if len(param_kwargs) == 1 and param_kwargs[0] in PULSE_DATA:
            pulse_name = param_kwargs[0]
            affected = await self.change_pulse_for_all(user_id_map, pulse_name)
            await self.send_message(
                f"所有已连接用户A、B通道波形已更改为{pulse_name}（共{affected}人）"
            )
            return
        if (
            len(param_kwargs) == 2
            and param_kwargs[0] in {"A", "B"}
            and param_kwargs[1] in PULSE_DATA
        ):
            channel = param_kwargs[0]
            pulse_name = param_kwargs[1]
            affected = await self.change_pulse_for_all(user_id_map, channel, pulse_name)
            await self.send_message(
                f"所有已连接用户通道{channel}波形已更改为{pulse_name}（共{affected}人）"
            )
            return
        await self.send_message("change命令格式错误")

    async def set_strength_for_all(self, user_id_map: dict, op_type, *args):
        mgr = _require_user_manager()
        affected = 0
        for user_id in mgr.get_all_users():
            user_conn = mgr.get_user_connection(user_id)
            commander = user_conn["commander"]
            if commander and commander.status_code == 2 and commander.client:
                try:
                    if len(args) == 1:
                        await commander.client.set_strength(Channel.A, op_type, int(args[0]))
                        await commander.client.set_strength(Channel.B, op_type, int(args[0]))
                    elif len(args) == 2:
                        if args[0] == "A":
                            await commander.client.set_strength(Channel.A, op_type, int(args[1]))
                        elif args[0] == "B":
                            await commander.client.set_strength(Channel.B, op_type, int(args[1]))
                    affected += 1
                except Exception as e:
                    _log.error("设置用户 %s 强度失败: %s", user_id_map.get(user_id, user_id), e)
        return affected

    async def set(self, user_id_map: dict, user_id_map_path: str):
        param_kwargs = self.kwargs.copy()
        if len(param_kwargs) == 1 and param_kwargs[0].isdigit():
            strength_value = int(param_kwargs[0])
            affected = await self.set_strength_for_all(
                user_id_map, StrengthOperationType.SET_TO, strength_value
            )
            await self.send_message(
                f"所有已连接用户通道A、B强度已设置至 {strength_value}（共{affected}人）"
            )
            return
        if (
            len(param_kwargs) == 2
            and param_kwargs[0] in {"A", "B"}
            and param_kwargs[1].isdigit()
        ):
            channel = param_kwargs[0]
            strength_value = int(param_kwargs[1])
            affected = await self.set_strength_for_all(
                user_id_map, StrengthOperationType.SET_TO, channel, strength_value
            )
            await self.send_message(
                f"所有已连接用户通道{channel}强度已设置至 {strength_value}（共{affected}人）"
            )
            return
        await self.send_message("set命令格式错误")

    async def increase(self, user_id_map: dict, user_id_map_path: str):
        param_kwargs = self.kwargs.copy()
        if len(param_kwargs) == 1 and param_kwargs[0].isdigit():
            strength_value = int(param_kwargs[0])
            affected = await self.set_strength_for_all(
                user_id_map, StrengthOperationType.INCREASE, strength_value
            )
            await self.send_message(
                f"所有已连接用户通道A、B强度已增加 {strength_value}（共{affected}人）"
            )
            return
        if (
            len(param_kwargs) == 2
            and param_kwargs[0] in {"A", "B"}
            and param_kwargs[1].isdigit()
        ):
            channel = param_kwargs[0]
            strength_value = int(param_kwargs[1])
            affected = await self.set_strength_for_all(
                user_id_map, StrengthOperationType.INCREASE, channel, strength_value
            )
            await self.send_message(
                f"所有已连接用户通道{channel}强度已增加 {strength_value}（共{affected}人）"
            )
            return
        await self.send_message("increase命令格式错误")

    async def decrease(self, user_id_map: dict, user_id_map_path: str):
        param_kwargs = self.kwargs.copy()
        if len(param_kwargs) == 1 and param_kwargs[0].isdigit():
            strength_value = int(param_kwargs[0])
            affected = await self.set_strength_for_all(
                user_id_map, StrengthOperationType.DECREASE, strength_value
            )
            await self.send_message(
                f"所有已连接用户通道A、B强度已降低 {strength_value}（共{affected}人）"
            )
            return
        if (
            len(param_kwargs) == 2
            and param_kwargs[0] in {"A", "B"}
            and param_kwargs[1].isdigit()
        ):
            channel = param_kwargs[0]
            strength_value = int(param_kwargs[1])
            affected = await self.set_strength_for_all(
                user_id_map, StrengthOperationType.DECREASE, channel, strength_value
            )
            await self.send_message(
                f"所有已连接用户通道{channel}强度已降低 {strength_value}（共{affected}人）"
            )
            return
        await self.send_message("decrease命令格式错误")

    def _connected_users(self):
        mgr = _require_user_manager()
        connected = []
        for user_id in mgr.get_all_users():
            u_conn = mgr.get_user_connection(user_id)
            commander = u_conn.get("commander")
            if (
                u_conn["status"] == "connected"
                and commander
                and getattr(commander, "client", None)
            ):
                connected.append(user_id)
        return connected

    async def random_increase(self, user_id_map: dict, user_id_map_path: str):
        mgr = _require_user_manager()
        user_conn = mgr.get_user_connection(self.qq_id)
        if user_conn["status"] != "connected" or not user_conn.get("commander") or not getattr(
            user_conn["commander"], "client", None
        ):
            await self.send_message("只有已连接的用户才可以使用随机增加命令")
            return

        connected_users = self._connected_users()
        if not connected_users:
            await self.send_message("当前没有已连接的用户，无法执行随机增加")
            return

        target_user_id = random.choice(connected_users)
        target_commander = mgr.get_user_connection(target_user_id)["commander"]
        target_client = target_commander.client

        param_kwargs = self.kwargs.copy()
        strength = target_commander.strength
        if len(param_kwargs) == 1 and param_kwargs[0].isdigit():
            value = int(param_kwargs[0])
            await target_client.set_strength(Channel.A, StrengthOperationType.INCREASE, value)
            await target_client.set_strength(Channel.B, StrengthOperationType.INCREASE, value)
            s = strength
            await self.send_message(
                f"已随机为用户{user_id_map.get(target_user_id, target_user_id)}增加A通道{value},B通道{value}\r\n"
                f"A当前:{s.a if s else '?'},A上限{s.a_limit if s else '?'}"
                f"B当前:{s.b if s else '?'},B上限{s.b_limit if s else '?'}"
            )
            return
        if (
            len(param_kwargs) == 2
            and param_kwargs[0] in {"A", "B"}
            and param_kwargs[1].isdigit()
        ):
            channel = param_kwargs[0]
            value = int(param_kwargs[1])
            ch = Channel.A if channel == "A" else Channel.B
            await target_client.set_strength(ch, StrengthOperationType.INCREASE, value)
            s = strength
            await self.send_message(
                f"已随机为用户{user_id_map.get(target_user_id, target_user_id)}增加{channel}通道{value}\r\n"
                f"A当前:{s.a if s else '?'},A上限{s.a_limit if s else '?'}"
                f"B当前:{s.b if s else '?'},B上限{s.b_limit if s else '?'}"
            )
            return
        await self.send_message("随机增加命令格式错误，应为「随机增加 20」或「随机增加 A 20」")

    async def random_decrease(self, user_id_map: dict, user_id_map_path: str):
        mgr = _require_user_manager()
        user_conn = mgr.get_user_connection(self.qq_id)
        if user_conn["status"] != "connected" or not user_conn.get("commander") or not getattr(
            user_conn["commander"], "client", None
        ):
            await self.send_message("只有已连接的用户才可以使用随机降低命令")
            return

        connected_users = self._connected_users()
        if not connected_users:
            await self.send_message("当前没有已连接的用户，无法执行随机降低")
            return

        target_user_id = random.choice(connected_users)
        target_commander = mgr.get_user_connection(target_user_id)["commander"]
        target_client = target_commander.client
        target_user_name = user_id_map.get(target_user_id, target_user_id)

        param_kwargs = self.kwargs.copy()
        if len(param_kwargs) == 1 and param_kwargs[0].isdigit():
            value = int(param_kwargs[0])
            await target_client.set_strength(Channel.A, StrengthOperationType.DECREASE, value)
            await target_client.set_strength(Channel.B, StrengthOperationType.DECREASE, value)
            await self.send_message(
                f"已随机为用户{target_user_name}降低A通道{value}，B通道{value}"
            )
            return
        if (
            len(param_kwargs) == 2
            and param_kwargs[0] in {"A", "B"}
            and param_kwargs[1].isdigit()
        ):
            channel = param_kwargs[0]
            value = int(param_kwargs[1])
            ch = Channel.A if channel == "A" else Channel.B
            await target_client.set_strength(ch, StrengthOperationType.DECREASE, value)
            await self.send_message(f"已随机为用户{target_user_name}降低{channel}通道{value}")
            return
        await self.send_message("随机降低命令格式错误，应为「随机降低 20」或「随机降低 A 20」")

    async def _random_all(self, user_id_map: dict, decrease: bool):
        mgr = _require_user_manager()
        label = "降低" if decrease else "增加"
        op = StrengthOperationType.DECREASE if decrease else StrengthOperationType.INCREASE

        user_conn = mgr.get_user_connection(self.qq_id)
        if user_conn["status"] != "connected" or not user_conn.get("commander") or not getattr(
            user_conn["commander"], "client", None
        ):
            await self.send_message(f"只有已连接的用户才可以使用随机{label}命令")
            return

        param_kwargs = self.kwargs.copy()
        if len(param_kwargs) != 1 or not param_kwargs[0].isdigit():
            await self.send_message(f"命令格式错误，应为「全体随机{label} 最大档位」")
            return
        max_num = int(param_kwargs[0])
        if max_num <= 0:
            await self.send_message("最大档位必须为正整数")
            return

        connected_users = self._connected_users()
        if not connected_users:
            await self.send_message(f"当前没有已连接的用户，无法执行全体随机{label}")
            return

        user_num_map = {uid: random.randint(1, max_num) for uid in connected_users}
        sorted_user_num = sorted(user_num_map.items(), key=lambda x: x[1])

        msg_list = []
        for user_id, num in sorted_user_num:
            commander = mgr.get_user_connection(user_id)["commander"]
            client = commander.client
            if client:
                await client.set_strength(Channel.A, op, num)
                await client.set_strength(Channel.B, op, num)
                msg_list.append(
                    f"玩家{user_id_map.get(user_id, user_id)}双通道{label}了{num}档位"
                )

        await self.send_message(
            f"本次全体随机{label}由{user_id_map.get(self.qq_id, self.qq_id)}执行\r\n"
            f"本次全体随机{label}结果(按档位升序):\r\n" + "\r\n".join(msg_list)
        )

    async def random_increase_all(self, user_id_map: dict, user_id_map_path: str):
        await self._random_all(user_id_map, decrease=False)

    async def random_decrease_all(self, user_id_map: dict, user_id_map_path: str):
        await self._random_all(user_id_map, decrease=True)

    async def status(self, user_id_map: dict, user_id_map_path: str):
        mgr = _require_user_manager()

        if self.size != 1:
            await self.send_message("status命令不应有参数")
            return

        users = mgr.get_all_users()
        if not users:
            await self.send_message("当前没有用户")
            return

        msg = "所有用户状态：\r\n"
        for user_id in users:
            user_conn = mgr.get_user_connection(user_id)
            commander = user_conn.get("commander")
            user_name = user_id_map.get(user_id, user_id)
            status_code = getattr(commander, "status_code", 0) if commander else 0
            if status_code == 0:
                msg += f"{user_name}：未连接\r\n"
            elif status_code == 1:
                msg += f"{user_name}：等待连接\r\n"
            elif status_code == 2:
                strength = getattr(commander, "strength", None)
                pa = getattr(commander, "current_pulses_A_name", "")
                pb = getattr(commander, "current_pulses_B_name", "")
                if strength:
                    msg += (
                        f"{user_name}：已连接\r\n"
                        f"  A通道：{strength.a} 上限{strength.a_limit}\r\n"
                        f"  B通道：{strength.b} 上限{strength.b_limit}\r\n"
                        f"  A波形：{pa}, B波形{pb}\r\n"
                    )
                else:
                    msg += f"{user_name}：已连接（无强度数据）\r\n"
            else:
                msg += f"{user_name}：未知状态\n"

        await self.send_message(msg)

    async def close(self, user_id_map: dict, user_id_map_path: str):
        self.close_tag = True
        self.pulse_close_tag = True
        await self.send_message("已发送断开连接信号，可能需要较长时间响应")

    async def setid2username(self, user_id_map: dict, user_id_map_path: str):
        if self.size != 2:
            await self.send_message("设置名称命令格式错误，应为：设置名称 名称")
            return

        new_name = self.kwargs[0]
        if os.path.exists(user_id_map_path):
            with open(user_id_map_path, encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f) or {}
        else:
            yaml_data = {}

        if "user_id_map" not in yaml_data:
            yaml_data["user_id_map"] = {}
        yaml_data["user_id_map"][self.qq_id] = new_name
        user_id_map[self.qq_id] = new_name

        try:
            dir_name = os.path.dirname(user_id_map_path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            with open(user_id_map_path, "w", encoding="utf-8") as f:
                yaml.dump(
                    yaml_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False
                )
            await self.send_message(f"已设置名称：{new_name}")
        except Exception as e:
            await self.send_message(f"保存名称失败：{e}")

    async def user_list(self, user_id_map: dict, user_id_map_path: str):
        mgr = _require_user_manager()

        users = mgr.get_all_users()
        if not users:
            await self.send_message("当前没有用户连接")
            return

        status_text = {
            "disconnected": "未连接",
            "connecting": "连接中",
            "connected": "已连接",
        }
        user_list_msg = "当前用户列表：\n"
        for user_id in users:
            user_conn = mgr.get_user_connection(user_id)
            if user_conn["status"] == "disconnected":
                continue
            st = status_text.get(user_conn["status"], "未知状态")
            user_list_msg += f"{user_id_map.get(user_id, user_id)} - 状态: {st}\n"

        await self.send_message(user_list_msg)

    async def help(self, user_id_map: dict, user_id_map_path: str):
        prefix = getattr(self, "_help_prefix", "dgkab")
        await self.send_message(
            f"这里是命令介绍喵~（前缀: {prefix}）\r\n\r\n"
            "「新建连接」命令用于连接app，无参数，每个用户都有独立的连接\r\n"
            "「设置强度」,「增加强度」,「降低强度」命令用于设定、增加、减小所有已连接用户的强度，"
            "如：设置强度 A 100 或 设置强度 100（同时设置双通道）\r\n"
            "「随机增加」,「随机降低」命令仅限已连接用户使用，格式如：随机增加 20 或 随机增加 A 20\r\n"
            "「当前状态」命令用于查看当前连接状况，强度大小，强度上限，无参数\r\n"
            "「改变波形」命令用于更改指定通道波形，如：改变波形 A 潮汐，波形名称列表如下：\r\n"
            "呼吸、潮汐、连击、快速按捏、按捏渐强、心跳节奏、压缩、节奏步伐、颗粒摩擦、"
            "渐变弹跳、波浪涟漪、雨水冲刷、变速敲击、信号灯、挑逗1、挑逗2\r\n"
            "「用户列表」命令用于查看所有当前用户及其连接状态\r\n"
            "「全体随机增加」,「全体随机降低」命令用于随机增加或降低所有已连接用户的强度\r\n"
            "「设置名称」设置自己的名称，格式如：设置名称 呱呱糕\r\n"
            "「帮助」命令用于查看所有命令\r\n"
        )


# 全局连接管理器，由插件初始化时赋值
user_manager: UserConnectionManager | None = None


def _require_user_manager() -> UserConnectionManager:
    if user_manager is None:
        raise RuntimeError("DGLAB user_manager 尚未初始化")
    return user_manager


def init_user_manager(base_port: int) -> UserConnectionManager:
    global user_manager
    user_manager = UserConnectionManager(base_port)
    return user_manager
