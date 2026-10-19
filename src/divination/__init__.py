"""算命子系统：排盘（bazi/ziwei）· 断命（rules）· 起卦（gua/meihua/xiaoliuren）· 解读（reader）。

ziwei 依赖 iztro-py，故意不在这里预导入——真没装时只影响紫微，
不会把整个算命包带崩（service 里是懒加载）。
"""

from . import bazi, gua, meihua, reader, rules, xiaoliuren  # noqa: F401

__all__ = ["bazi", "gua", "meihua", "xiaoliuren", "reader", "rules"]
