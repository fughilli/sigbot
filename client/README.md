# sigbot-client

Zero-dependency Python client for the [sigbot](../sigbot) service API. Each
sigbot *service* is one Signal group chat with its own persona; an API key
(minted in the sigbot dashboard) scopes this client to that group.

## Install

Build the wheel from the repo and install it on the host or in any container:

```sh
pip wheel --no-deps ./client -w dist/
pip install dist/sigbot_client-*.whl
```

(Or `pip install ./client` straight from the source tree.)

## Use

```python
from sigbot_client import ServiceClient

bot = ServiceClient("http://myhost:8100", api_key="sb_...")

bot.service()                    # {'name': ..., 'label': ..., 'group_name': ...}
bot.send("deploy finished ✅")   # post into the group as the bot
bot.send("raw text", prefix=False)  # suppress the [label] prefix for this message

# Read the group's message log; poll incrementally with after_id:
msgs = bot.messages(limit=50)
newer = bot.messages(after_id=msgs[-1]["id"]) if msgs else []
```

Errors surface as `SigbotApiError` with `.status` and `.message` (e.g. 401 for
a revoked key).
