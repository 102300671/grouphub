#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
proxy_failover.py —— 本机 HTTP 代理故障转移中继

适用场景：Windows 宿主上同时装了 Clash(7890) 和 v2rayN(10808)，
平时只开其中一个；WSL 里的 SearXNG 需要一个"永远活着"的代理地址。

工作方式：
  1. 只监听 127.0.0.1:7899（不对 LAN/公网暴露，无鉴权也安全）；
  2. 每 CHECK_INTERVAL 秒 TCP 探测一次全部上游（探测超时 1s，
     Windows 防火墙对未监听端口是丢包而非 RST，必须靠超时判定）；
  3. 连续 FAIL_THRESHOLD 次失败才摘除，探测成功一次即恢复；
  4. 入站连接是纯 TCP 透传，兼容 HTTP 代理的明文 GET 与 CONNECT 隧道；
     按固定优先级选第一个存活上游，连接建立后失败会即时换一个。

SearXNG 侧只需配置：
  outgoing.proxies:
    all://: http://127.0.0.1:7899

零第三方依赖，直接用 systemd 托管（见 proxy-failover.service）。
"""

import asyncio
import contextlib
import logging
import os
import time

# ---- 配置区（可用同名环境变量覆盖：LISTEN_PORT / UPSTREAMS 等）-------------
LISTEN_HOST = os.environ.get("LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "7899"))

# (名称, 主机, 端口)；顺序即优先级，两个都开时用最前面的
# UPSTREAMS 环境变量格式： name=host:port,name=host:port
UPSTREAMS = [
    ("clash", "172.31.208.1", 7890),
    ("v2rayn", "172.31.208.1", 10808),
]
if os.environ.get("UPSTREAMS"):
    UPSTREAMS = []
    for item in os.environ["UPSTREAMS"].split(","):
        name, hp = item.strip().split("=", 1)
        host, port = hp.rsplit(":", 1)
        UPSTREAMS.append((name.strip(), host.strip(), int(port)))

CHECK_INTERVAL = float(os.environ.get("CHECK_INTERVAL", "5.0"))   # 健康检查周期（秒）
CHECK_TIMEOUT = float(os.environ.get("CHECK_TIMEOUT", "1.0"))     # 单次探测超时（秒）
FAIL_THRESHOLD = int(os.environ.get("FAIL_THRESHOLD", "2"))       # 连续失败 N 次才摘除
DIAL_TIMEOUT = float(os.environ.get("DIAL_TIMEOUT", "3.0"))       # 转发时连上游超时（秒）
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("proxy-failover")


class UpstreamState:
    def __init__(self, name, host, port):
        self.name = name
        self.host = host
        self.port = port
        self.alive = False
        self.fails = 0
        self.last_change = time.time()

    @property
    def label(self):
        return f"{self.name}({self.host}:{self.port})"

    def set_alive(self, alive):
        if alive == self.alive:
            return
        self.alive = alive
        self.last_change = time.time()
        if alive:
            log.info("上游恢复: %s", self.label)
        else:
            log.warning("上游摘除: %s（连续 %s 次探测失败）", self.label, self.fails)


STATES = [UpstreamState(*u) for u in UPSTREAMS]


async def probe(up: UpstreamState) -> bool:
    try:
        fut = asyncio.open_connection(up.host, up.port)
        reader, writer = await asyncio.wait_for(fut, timeout=CHECK_TIMEOUT)
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def health_check_loop():
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        await run_one_check()


def pick(exclude=None):
    """按优先级返回第一个存活上游；exclude 中的本次连接内不再重试。"""
    for up in STATES:
        if up.alive and up.label not in exclude:
            return up
    return None


async def pipe(reader, writer):
    """单向搬运；读端 EOF 时只半关闭写端，让反方向数据继续流完。"""
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (OSError, RuntimeError):
        with contextlib.suppress(Exception):
            writer.close()


async def handle(client_reader, client_writer):
    peer = client_writer.get_extra_info("peername")
    tried = set()
    upstream_writer = upstream_reader = chosen = None
    try:
        # 读第一批数据前先建立上游（HTTP 代理请求总是客户端先说话）
        while True:
            chosen = pick(exclude=tried)
            if chosen is None:
                log.error("无可用上游，拒绝连接 %s", peer)
                client_writer.close()
                return
            try:
                fut = asyncio.open_connection(chosen.host, chosen.port)
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    fut, timeout=DIAL_TIMEOUT
                )
                break
            except (OSError, asyncio.TimeoutError):
                # 转发瞬间才发现端口已关：即时摘除，马上换另一个
                tried.add(chosen.label)
                chosen.fails = FAIL_THRESHOLD
                chosen.set_alive(False)
                log.warning("连接 %s 失败，尝试下一个上游", chosen.label)

        await asyncio.gather(
            pipe(client_reader, upstream_writer),
            pipe(upstream_reader, client_writer),
        )
    except (OSError, RuntimeError):
        pass
    finally:
        for w in (client_writer, upstream_writer):
            if w is not None:
                with contextlib.suppress(Exception):
                    w.close()


async def run_one_check():
    for up in STATES:
        ok = await probe(up)
        if ok:
            up.fails = 0
            up.set_alive(True)
        else:
            up.fails += 1
            if up.fails >= FAIL_THRESHOLD:
                up.set_alive(False)


async def main():
    # 启动前先探一轮，避免服务已监听但还没有可用上游
    await run_one_check()
    server = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT)
    log.info(
        "代理故障转移中继已启动: 监听 %s:%s -> %s",
        LISTEN_HOST,
        LISTEN_PORT,
        " / ".join(u.label for u in STATES),
    )
    log.info("健康检查: 每 %ss 一次, 探测超时 %ss, 连续失败 %s 次摘除",
             CHECK_INTERVAL, CHECK_TIMEOUT, FAIL_THRESHOLD)
    async with server:
        await asyncio.gather(server.serve_forever(), health_check_loop())


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
