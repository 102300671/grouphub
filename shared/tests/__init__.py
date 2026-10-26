"""共享包单测（unittest，三个环境都无 pytest）。

跑法（仓库根目录下）::

    python3 -m unittest discover -s shared/tests -t .

用例里的期望值是「M1 迁移前旧实现」的输出，逐字冻结在 fixtures/ 下。
"""
