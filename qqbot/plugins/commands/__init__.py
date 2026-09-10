"""命令实现包。

每个子模块导出 COMMANDS（命令定义元组）与对应 handler 函数，
由 cli_router 统一收集、注册并分发。本包不自行注册 nonebot matcher。
"""
