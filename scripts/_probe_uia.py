import sys, time
sys.path.insert(0, r"D:\鲸鱼娘")
from wechatauto.uia_driver import WeChatUIA
t0=time.time()
eng=WeChatUIA()
print("window:", eng.ensure_window(), "materialized:", eng.is_materialized())
print("elapsed %.1fs" % (time.time()-t0))
if not eng.is_materialized():
    print("ensure_materialized:", eng.ensure_materialized(timeout=6.0), eng.is_materialized())
