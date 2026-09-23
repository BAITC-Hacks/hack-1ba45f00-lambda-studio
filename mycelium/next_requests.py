"""«Что запросить дальше» (CLAUDE.md §12): где выгрузка обрывается на самом интересном месте.

outgoing — узлы границы выборки (4-е колено): исходящие не собирались; ранжируем по сумме входа, затем
           по числу плательщиков — куда пришло больше всего денег, там обрыв дороже всего.
incoming — не-курьеры, которые отдали больше, чем видно входящих: приток вне выборки.
"""
from __future__ import annotations

import pandas as pd

from mycelium.evidence import fmt_kzt, n_payers

MAX_ROWS = 30
MAX_INCOMING = 10
COLUMNS = ["gid", "request_type", "reason", "in_kzt", "in_deg", "seed_reach"]


def build_requests(df: pd.DataFrame) -> pd.DataFrame:
    """Схема next_requests.csv."""
    d = df.assign(_gid=df.index)
    inc = d[d.inflow_outside_sample & (d.out_kzt > 0)] \
        .sort_values(["out_kzt", "_gid"], ascending=[False, True]).head(MAX_INCOMING)
    out = d[d.truncated & (d.in_deg > 0)] \
        .sort_values(["in_kzt", "in_deg", "_gid"], ascending=[False, False, True]) \
        .head(MAX_ROWS - len(inc))
    rows = []
    for g, r in out.iterrows():
        rows.append({"gid": int(g), "request_type": "outgoing",
                     "reason": (f"граница выборки: получено {fmt_kzt(r.in_kzt)} от {n_payers(r.in_deg)}, "
                                f"сюда доходят деньги курьеров: {int(r.seed_reach)}; исходящие не видны — "
                                f"запросите исходящие"),
                     "in_kzt": round(float(r.in_kzt), 2), "in_deg": int(r.in_deg), "seed_reach": int(r.seed_reach)})
    for g, r in inc.iterrows():
        rows.append({"gid": int(g), "request_type": "incoming",
                     "reason": (f"отдал {fmt_kzt(r.out_kzt)} при видимом входе {fmt_kzt(r.in_kzt)} — "
                                f"приток вне выборки, запросите входящие"),
                     "in_kzt": round(float(r.in_kzt), 2), "in_deg": int(r.in_deg), "seed_reach": int(r.seed_reach)})
    return pd.DataFrame(rows, columns=COLUMNS)
