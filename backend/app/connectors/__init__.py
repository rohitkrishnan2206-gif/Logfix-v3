"""Connector factory: (source row, decrypted secrets) -> Connector."""
from __future__ import annotations

import json

from .base import Connector, ConnectorError


def build_connector(source: dict, secrets: dict) -> Connector:
    cfg = source.get("config") or {}
    if isinstance(cfg, str):
        cfg = json.loads(cfg or "{}")
    cloud = source["cloud"]
    if cloud == "aws":
        from .aws import AwsConnector
        return AwsConnector(cfg, secrets)
    if cloud == "azure":
        from .azure import AzureConnector
        return AzureConnector(cfg, secrets)
    if cloud == "gcp":
        from .gcp import GcpConnector
        return GcpConnector(cfg, secrets)
    if cloud == "demo":
        from .demo import DemoConnector
        return DemoConnector(cfg, secrets)
    raise ConnectorError("config", f"Unknown cloud: {cloud}")
