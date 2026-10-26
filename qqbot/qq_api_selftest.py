"""自检脚本：验证 raw HTTP 路径（token / URL 构造 / force_verify 参数）真的能打到 QQ API。

**不发送任何消息** —— 用假的 group_openid，真 token，真请求。
预期：拿到一个来自 QQ 的 HTTP 错误响应（说明请求已到达并被平台处理），
而不是本地 TypeError / 连接异常。
"""
import asyncio
import json

import httpx
from yarl import URL

API_BASE = "https://api.sgroup.qq.com"
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"


def load_env(path=".env.prod"):
    env = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


async def main():
    env = load_env()
    raw = env.get("QQ_BOTS", "")
    print("QQ_BOTS 原始:", raw[:70])
    try:
        bots = json.loads(raw)
    except Exception:
        bots = []
    if isinstance(bots, dict):
        bots = [bots]
    b = bots[0] if bots else {}
    appid = b.get("id") or b.get("appid") or b.get("appId")
    secret = b.get("secret") or b.get("appSecret")
    print("appid:", appid, "| secret 有:", bool(secret))

    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(TOKEN_URL, json={"appId": appid, "clientSecret": secret})
        d = r.json()
        token = d.get("access_token")
        print("取 token:", "OK" if token else "FAIL", "| code:", d.get("code"), "|", d.get("message", ""))
        if not token:
            return

        # ⚠️ 这就是刚才踩的坑：适配器返回 yarl.URL，httpx 不认，必须先 str()
        joined = URL(API_BASE) / "v2" / "groups" / "FAKE_SELFTEST" / "messages"
        print("yarl 类型:", type(joined).__name__)
        try:
            httpx.URL(joined)
            print("!! 直接传 yarl.URL 竟然没报错")
        except TypeError as exc:
            print("OK 直接传 yarl.URL 会报 TypeError（印证刚才那个 bug）:", str(exc)[:60])

        url = httpx.URL(str(joined))
        r2 = await c.post(
            url,
            json={
                "msg_type": 2,
                "markdown": {
                    "content": "**selftest**\n\n![t](http://59.110.163.88/m0test/blocks.png)"
                },
                "msg_id": "FAKE_MSG_ID",
                "msg_seq": 1,
                "force_verify_image_resource": True,
            },
            headers={"Authorization": f"QQBot {token}"},
        )
        print("API HTTP:", r2.status_code)
        print("body:", r2.text[:400])


asyncio.run(main())
