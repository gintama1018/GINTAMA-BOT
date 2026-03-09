"""
src/tool_schemas.py — Pydantic argument schemas for every JARVIS tool.

Usage in ToolExecutor.execute():
    schema = TOOL_SCHEMAS.get(tool_name)
    if schema:
        validated = schema(**raw_args)   # raises ValidationError on bad args
        args = validated.model_dump()

This layer catches bad LLM-generated arguments before they hit real OS calls,
returning a clean error message to the agent instead of a raw Python traceback.
"""

from __future__ import annotations

from typing import Optional

try:
    from pydantic import BaseModel, Field, field_validator
    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False
    # Minimal stub so imports never crash even without pydantic installed.
    class BaseModel:  # type: ignore
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
        def model_dump(self):
            return vars(self)
    def Field(*a, **kw):  # type: ignore
        return None
    def field_validator(*a, **kw):  # type: ignore
        def dec(fn): return fn
        return dec

# ─────────────────────────────────────────────────────────────────────────── #
#  Phone tool schemas                                                          #
# ─────────────────────────────────────────────────────────────────────────── #

class PhoneLaunchArgs(BaseModel):
    app: str = Field(..., description="App name, e.g. 'camera', 'youtube', 'whatsapp'")

    @field_validator("app")
    @classmethod
    def app_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("app name cannot be empty")
        return v.strip().lower()


class PhoneVolumeArgs(BaseModel):
    level: str = Field(..., description="Volume 0-15 as string")

    @field_validator("level")
    @classmethod
    def valid_level(cls, v: str) -> str:
        try:
            n = int(v)
        except ValueError:
            raise ValueError(f"level must be a number 0-15, got '{v}'")
        if not 0 <= n <= 15:
            raise ValueError(f"level must be 0-15, got {n}")
        return str(n)


class PhoneNotifyArgs(BaseModel):
    message: str = Field(..., min_length=1, description="Notification text")


# ─────────────────────────────────────────────────────────────────────────── #
#  System (local PC) tool schemas                                              #
# ─────────────────────────────────────────────────────────────────────────── #

class SystemOpenArgs(BaseModel):
    app: str = Field(..., description="Application name to open")

    @field_validator("app")
    @classmethod
    def app_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("app name cannot be empty")
        return v.strip()


class SystemNotifyArgs(BaseModel):
    message: str = Field(..., min_length=1)
    title: str = Field("JARVIS", description="Notification title")


class SystemRunArgs(BaseModel):
    command: str = Field(..., min_length=1, description="Shell command to execute")

    @field_validator("command")
    @classmethod
    def not_dangerous(cls, v: str) -> str:
        _BLOCKED = [
            "rm -rf", "format", "mkfs", "del /f /s /q",
            "rmdir /s /q", "rd /s /q", ":(){:|:&}",
            "shutdown /r", "shutdown /s", "reboot",
        ]
        lower = v.lower()
        for blocked in _BLOCKED:
            if blocked in lower:
                raise ValueError(
                    f"Command contains blocked pattern '{blocked}'. "
                    "Use a safer alternative."
                )
        return v


# ─────────────────────────────────────────────────────────────────────────── #
#  Device tool schemas                                                         #
# ─────────────────────────────────────────────────────────────────────────── #

class DeviceInfoArgs(BaseModel):
    device: str = Field("laptop", description="Registered device name")


# ─────────────────────────────────────────────────────────────────────────── #
#  File tool schemas                                                           #
# ─────────────────────────────────────────────────────────────────────────── #

class FileLsArgs(BaseModel):
    path: str = Field(".", description="Directory path")


class FileReadArgs(BaseModel):
    path: str = Field(..., min_length=1, description="File path to read")


# ─────────────────────────────────────────────────────────────────────────── #
#  Web tool schemas                                                            #
# ─────────────────────────────────────────────────────────────────────────── #

class WebSearchArgs(BaseModel):
    query: str = Field(..., min_length=1, description="Search query")


class BrowserOpenArgs(BaseModel):
    url: str = Field(..., min_length=6, description="URL to open")

    @field_validator("url")
    @classmethod
    def must_be_http(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError(f"URL must start with http:// or https://, got '{v}'")
        return v


# ─────────────────────────────────────────────────────────────────────────── #
#  Scheduler schema                                                            #
# ─────────────────────────────────────────────────────────────────────────── #

class ScheduleTaskArgs(BaseModel):
    name: str = Field(..., min_length=1)
    time: str = Field(..., description="HH:MM format")
    command: str = Field(..., min_length=1)

    @field_validator("time")
    @classmethod
    def valid_time(cls, v: str) -> str:
        import re
        if not re.match(r"^\d{1,2}:\d{2}$", v):
            raise ValueError(f"time must be HH:MM format, got '{v}'")
        h, m = v.split(":")
        if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            raise ValueError(f"Invalid time '{v}'")
        return v


# ─────────────────────────────────────────────────────────────────────────── #
#  Master lookup: tool_name → schema class (None = no args required)          #
# ─────────────────────────────────────────────────────────────────────────── #

TOOL_SCHEMAS: dict = {
    # Phone
    "phone_launch":      PhoneLaunchArgs,
    "phone_screenshot":  None,
    "phone_battery":     None,
    "phone_volume":      PhoneVolumeArgs,
    "phone_lock":        None,
    "phone_notify":      PhoneNotifyArgs,
    # System
    "system_info":       None,
    "system_screenshot": None,
    "system_open":       SystemOpenArgs,
    "system_notify":     SystemNotifyArgs,
    "system_run":        SystemRunArgs,
    # Remote device
    "device_info":       DeviceInfoArgs,
    # Files
    "file_ls":           FileLsArgs,
    "file_read":         FileReadArgs,
    # Web
    "web_search":        WebSearchArgs,
    "browser_open":      BrowserOpenArgs,
    # Scheduler
    "schedule_task":     ScheduleTaskArgs,
}


def validate(tool_name: str, args: dict) -> tuple[dict, str | None]:
    """
    Validate args against the schema for tool_name.

    Returns (validated_args_dict, None) on success.
    Returns (args, error_message) on failure.
    """
    schema_cls = TOOL_SCHEMAS.get(tool_name)
    if schema_cls is None:
        return args, None  # no schema → pass through

    try:
        validated = schema_cls(**args)
        return validated.model_dump(), None
    except Exception as exc:
        return args, f"Invalid arguments for '{tool_name}': {exc}"
