"""Dependency-free, real character-bigram multinomial naive Bayes inference.

Training examples are authored synthetic text, NOT images or field observations.
The model is a transparent functional demo, not a certified safety detector.
"""
from collections import Counter
import hashlib
import math
import re
import time

TRAINING = {
    "烟雾风险": ["茶水间出现烟雾", "机房有浓烟和烧焦味", "走廊发现冒烟", "烟感报警发现明火", "电线过热散发烟气", "办公室有火焰和浓烟", "插座着火冒烟", "仓库发现火情", "设备燃烧产生烟雾", "垃圾桶有烟火", "配电箱冒出白烟", "楼道里有焦糊味"],
    "通道占用": ["消防通道被纸箱堵住", "安全出口堆放杂物", "走廊通道有货物挡路", "逃生路线被占用", "疏散出口被堵塞", "门口堆满货箱无法通行", "安全通道有障碍物", "楼梯间堆放物品", "消防出口堵住了", "过道被桌椅占满", "疏散通道阻塞", "应急出口有杂物"],
    "门禁异常": ["机房门没有关好", "下班以后门禁仍然敞开", "门锁损坏无法关闭", "无人时办公室大门开着", "未授权人员进入机房", "门禁失效有陌生人进入", "门被强行打开", "夜间门长时间未关闭", "门禁报警锁舌故障", "安全门没有上锁", "门锁失灵有人闯入", "办公室门禁离线"],
    "正常状态": ["办公室一切正常", "通道畅通没有杂物", "门禁关闭设备正常", "机房运行平稳无告警", "巡检完成未发现异常", "地面整洁出口通畅", "空调照明工作正常", "安全检查通过", "正常办公人员有序通行", "环境温度正常设备完好", "所有门窗已经关闭", "办公室日常巡查无异常"],
}
DEMO_EVENTS = [
    "办公室打印机旁出现浓烟和焦糊味", "消防出口旁纸箱堆放阻碍通行", "夜间巡检发现机房门长时间敞开",
    "会议室设备运行平稳巡检无异常", "配电箱线路过热开始冒烟", "楼梯间疏散通道被桌椅堵塞",
    "门禁锁舌损坏大门无法关闭", "办公区域整洁安全出口通畅",
]

def tokens(text):
    chars = re.sub(r"\s+", "", text.lower())
    return list(chars) + [chars[i:i + 2] for i in range(len(chars) - 1)]

class Classifier:
    def __init__(self):
        self.counts = {label: Counter(t for x in rows for t in tokens(x)) for label, rows in TRAINING.items()}
        self.vocabulary = set(t for c in self.counts.values() for t in c)
        self.totals = {k: sum(c.values()) for k, c in self.counts.items()}

    def predict(self, text):
        counts = Counter(t for t in tokens(text) if t in self.vocabulary)
        scores = {k: sum(n * math.log((c[t] + 1) / (self.totals[k] + len(self.vocabulary))) for t, n in counts.items()) for k, c in self.counts.items()}
        peak = max(scores.values())
        probs = {k: math.exp(v - peak) for k, v in scores.items()}
        total = sum(probs.values())
        label = max(probs, key=probs.get)
        return {"text": text, "label": label, "confidence": round(probs[label] / total, 4), "review_required": True}

MODEL = Classifier()

def run_task(payload):
    start = time.perf_counter()
    predictions = [MODEL.predict(x) for x in payload["events"]]
    # Optional bounded CPU load for scheduling demonstrations. This is measured
    # separately and is never described as neural-model inference time.
    loops = payload.get("compute_loops", 0)
    digest = hashlib.sha256("\n".join(payload["events"]).encode()).digest()
    before_load = time.perf_counter()
    for _ in range(loops):
        digest = hashlib.sha256(digest).digest()
    return {"predictions": predictions, "model": "char-bigram-naive-bayes-v1", "training_source": "48 authored synthetic examples", "load_loops": loops, "load_ms": round((time.perf_counter() - before_load) * 1000, 3), "compute_ms": round((time.perf_counter() - start) * 1000, 3), "digest": digest.hex()}
