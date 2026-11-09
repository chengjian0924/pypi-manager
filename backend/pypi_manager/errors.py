# -*- coding: utf-8 -*-
"""业务异常定义。"""
from __future__ import annotations


class ServiceError(Exception):
    """带 HTTP 状态码的业务异常，供 routes 层直接映射为响应。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message
