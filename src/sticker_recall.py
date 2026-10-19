# -*- coding: utf-8 -*-
"""表情向量召回（emoji-recall 思路的本地实现）。

不依赖任何标签：索引就是表情的原始文案（name / 台词 / 梗说明）。
查询用最近对话文本，取余弦相似度最高的 topK；低于 min_score 视为没有合适表情。
模型：BAAI/bge-small-zh-v1.5（CPU，首次加载约 2-4 秒，向量缓存到 data/）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


class StickerRecall:
    def __init__(self, items_file: str, model: str = "BAAI/bge-small-zh-v1.5",
                 top_k: int = 4, min_score: float = 0.45,
                 emb_cache: str = "./data/sticker_emb.npz",
                 root: str | None = None):
        self.items_file = Path(items_file)
        self.root = Path(root) if root else self.items_file.parent
        self.model_id = model
        self.top_k = max(1, int(top_k))
        self.min_score = float(min_score)
        self.emb_cache = Path(emb_cache)
        self.items: list[dict] = []
        self._E = None
        self._tok = None
        self._model = None
        self._error = None
        self._ready = False

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        if not self._ready:
            self._ready = True
            try:
                self._ensure()
            except Exception as e:  # noqa: BLE001
                self._error = str(e)[:120]
                self.items = []
        return bool(self.items) and self._E is not None

    def _ensure(self) -> None:
        if not self.items_file.is_file():
            return
        data = json.loads(self.items_file.read_text(encoding="utf-8"))
        self.items = [x for x in data if x.get("file")]
        texts = []
        for x in self.items:
            parts = [x.get("name") or "", x.get("caption") or "", (x.get("story") or "")[:80]]
            texts.append(" ".join(p for p in parts if p).strip() or "鲸鱼娘表情包")

        import torch
        from transformers import AutoModel, AutoTokenizer

        # 注意：tokenizer/model 必须先加载——命中向量缓存时也要用它们编码 query
        self._tok = AutoTokenizer.from_pretrained(self.model_id)
        self._model = AutoModel.from_pretrained(self.model_id).eval()

        if self.emb_cache.is_file():
            z = np.load(self.emb_cache, allow_pickle=True)
            if len(z["texts"]) == len(texts) and str(z["model"]) == self.model_id:
                self._E = z["E"].astype(np.float32)
                return
        out = []
        with torch.no_grad():
            for i in range(0, len(texts), 32):
                batch = texts[i:i + 32]
                inp = self._tok(batch, padding=True, truncation=True,
                                max_length=96, return_tensors="pt")
                h = self._model(**inp).last_hidden_state
                mask = inp["attention_mask"].unsqueeze(-1).float()
                v = (h * mask).sum(1) / mask.sum(1).clamp(min=1e-6)
                v = torch.nn.functional.normalize(v, dim=-1)
                out.append(v.numpy())
        self._E = np.vstack(out).astype(np.float32)
        self.emb_cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(self.emb_cache, E=self._E,
                            texts=np.array(texts, dtype=object), model=self.model_id)

    def _encode_query(self, text: str) -> np.ndarray:
        import torch
        inp = self._tok([text], padding=True, truncation=True, max_length=64, return_tensors="pt")
        with torch.no_grad():
            h = self._model(**inp).last_hidden_state
            mask = inp["attention_mask"].unsqueeze(-1).float()
            v = (h * mask).sum(1) / mask.sum(1).clamp(min=1e-6)
            v = torch.nn.functional.normalize(v, dim=-1)
        return v.numpy().astype(np.float32)[0]

    # ------------------------------------------------------------------
    def recall(self, query: str, k: int | None = None) -> list[dict]:
        if not self.available or not (query or "").strip():
            return []
        q = self._encode_query(str(query)[-200:])
        scores = self._E @ q
        idx = np.argsort(-scores)[: max(1, int(k or self.top_k))]
        out = []
        for j in idx:
            it = dict(self.items[int(j)])
            it["score"] = float(scores[int(j)])
            out.append(it)
        return out

    def best(self, query: str) -> dict | None:
        hits = self.recall(query, 1)
        if hits and hits[0]["score"] >= self.min_score:
            return hits[0]
        return None

    def abs_path(self, item: dict) -> Path:
        return self.root / str(item["file"])
