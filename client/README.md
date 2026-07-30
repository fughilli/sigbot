# sigbot-client

Zero-dependency Python client for the
[sigbot](https://github.com/fughilli/sigbot) service API. Each
sigbot *service* is one Signal group chat with its own persona; an API key
(minted in the sigbot dashboard) scopes this client to that group.

## Install

```sh
pip install sigbot-client
```

(Or grab the wheel from the GitHub releases page, or build it from the repo
with `bazel build //client:sigbot_client_wheel` — the py_wheel target in
BUILD.bazel is the package's single source of metadata.)

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
