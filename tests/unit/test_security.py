from uuid import uuid4

import pytest
from pydantic import BaseModel

from lifeos.core.config import Settings
from lifeos.core.tool_registry import ToolContext, ToolDefinition, ToolRegistry
from lifeos.integrations.media.storage import LocalStorage
from lifeos.integrations.zulip import parse_command, reply_target
from lifeos.security.access import RateLimiter


@pytest.mark.parametrize(
    "key", ["../secret", "x/y", f"{uuid4()}/../secret", f"{uuid4()}/../../.env"]
)
def test_path_traversal(tmp_path, key):
    with pytest.raises(ValueError):
        LocalStorage(tmp_path).path(key)


def test_private_dgx_required():
    with pytest.raises(ValueError, match="private VPN"):
        Settings(_env_file=None, dgx_base_url="http://10.0.0.1:8000/v1")


def test_rate_limit():
    limiter = RateLimiter(1)
    limiter.check(123)
    with pytest.raises(ValueError, match="Rate"):
        limiter.check(123)


def test_commands_and_group_privacy():
    assert parse_command("@**LifeOS** /short habits") == ("/short", "habits")
    with pytest.raises(ValueError):
        parse_command("/execute rm -rf /")
    with pytest.raises(PermissionError):
        reply_target(
            {
                "type": "private",
                "sender_id": 1,
                "display_recipient": [{"id": 1}, {"id": 2}, {"id": 3}],
            },
            3,
        )


async def test_tools_enforce_permissions_and_approval():
    class Input(BaseModel):
        value: int

    async def handler(args, context):
        return args.value

    audits = []

    async def audit(*args):
        audits.append(args)

    registry = ToolRegistry(audit)
    definition = ToolDefinition("save", "Safe save", Input, frozenset({"save"}), "write", handler)
    registry.register(definition)
    with pytest.raises(ValueError, match="Duplicate"):
        registry.register(definition)
    with pytest.raises(PermissionError):
        await registry.execute("save", {"value": 1}, ToolContext(uuid4(), 123, frozenset()))
    with pytest.raises(PermissionError):
        await registry.execute("save", {"value": 1}, ToolContext(uuid4(), 123, frozenset({"save"})))
    assert (
        await registry.execute(
            "save", {"value": 2}, ToolContext(uuid4(), 123, frozenset({"save"}), True)
        )
        == 2
    )
    assert len(audits) == 1
