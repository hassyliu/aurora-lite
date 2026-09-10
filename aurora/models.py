import ipaddress
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def host_value(value):
    value = value.strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        if len(value) <= 253 and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value):
            if all(0 < len(label) <= 63 and not label.startswith('-') and not label.endswith('-')
                   for label in value.split('.')):
                return value.lower()
        raise ValueError("请输入有效的 IP 或域名，不包含协议、端口和路径")


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class Probe(BaseModel):
    host: str
    port: int = Field(default=22, ge=1, le=65535)

    _host = field_validator('host')(host_value)


class ServerInput(Probe):
    name: str = Field(min_length=1, max_length=80)
    username: str = Field(default="root", pattern=r"^[a-zA-Z_][a-zA-Z0-9_.-]{0,63}$")
    auth_type: Literal['password', 'key'] = 'password'
    credential: str = Field(default='', max_length=32768)
    passphrase: str = Field(default='', max_length=256)
    fingerprint: str = Field(pattern=r"^SHA256:[A-Za-z0-9+/]{43}$")
    notes: str = Field(default='', max_length=1000)


class DestinationInput(Probe):
    name: str = Field(min_length=1, max_length=80)
    port: int = Field(ge=1, le=65535)
    notes: str = Field(default='', max_length=1000)

    @field_validator('name')
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip():
            raise ValueError('请输入落地名称')
        return value.strip()


class DestinationOrder(BaseModel):
    ids: list[str] = Field(max_length=2000)

    @field_validator('ids')
    @classmethod
    def valid_ids(cls, values):
        if len(values) != len(set(values)) or any(not re.fullmatch('[a-f0-9]{12}', v) for v in values):
            raise ValueError('排序列表包含重复或无效的落地 ID')
        return values


class RuleInput(BaseModel):
    server_id: str = Field(pattern=r"^[a-f0-9]{12}$")
    name: str = Field(min_length=1, max_length=80)
    method: Literal['iptables', 'gost']
    protocol: Literal['tcp', 'udp', 'both'] = 'tcp'
    listen_ip: str = '0.0.0.0'
    listen_port: int = Field(ge=1, le=65535)
    target_host: str
    target_port: int = Field(ge=1, le=65535)
    notes: str = Field(default='', max_length=1000)

    _target = field_validator('target_host')(host_value)

    @field_validator('listen_ip')
    @classmethod
    def valid_listen(cls, value):
        address = ipaddress.ip_address(value.strip())
        if address.is_multicast:
            raise ValueError('监听地址不能是组播地址')
        return str(address)

    @model_validator(mode='after')
    def target_family(self):
        if self.method == 'iptables':
            try:
                target = ipaddress.ip_address(self.target_host)
            except ValueError:
                raise ValueError('iptables 目标需要固定 IP；域名目标请使用 GOST') from None
            if target.version != ipaddress.ip_address(self.listen_ip).version:
                raise ValueError('iptables 入站和目标需使用相同 IP 版本；跨 IPv4/IPv6 请使用 GOST')
            if target.is_unspecified or target.is_multicast or target.is_loopback:
                raise ValueError('iptables 目标必须是可路由的远端单播地址')
        return self
