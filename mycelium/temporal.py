"""Временные метрики узла (CLAUDE.md §7): удержание, «пришло и сразу ушло», синхронный сбор."""
from __future__ import annotations

import numpy as np
import pandas as pd

from mycelium import config


def node_temporal(T: pd.DataFrame) -> pd.DataFrame:
    """index=gid; hold_median_days, fast_pass_share, sync_max_payers."""
    outs = T.groupby("src")["date"].apply(lambda s: np.sort(s.values))
    hold, fast = {}, {}
    for g, grp in T.groupby("dst"):
        if g not in outs.index:
            continue
        od = outs[g]
        idx = np.searchsorted(od, grp.date.values)
        h, w = [], []
        for i, d, s in zip(idx, grp.date.values, grp.sum_kzt.values):
            if i < len(od):
                h.append((od[i] - d) / np.timedelta64(1, "D"))
                w.append(s)
        if h:
            hold[g] = float(np.median(h))
            fast[g] = float(sum(s for x, s in zip(h, w) if x <= config.FAST_PASS_DAYS) / grp.sum_kzt.sum())
    sync = T.groupby(["dst", "date"])["src"].nunique().groupby("dst").max()
    res = pd.DataFrame({"hold_median_days": pd.Series(hold, dtype=float),
                        "fast_pass_share": pd.Series(fast, dtype=float)})
    res = res.join(sync.rename("sync_max_payers"), how="outer")
    res["sync_max_payers"] = res.sync_max_payers.fillna(0).astype(int)
    res.index.name = "gid"
    return res
