"""Which number on a pick actually predicts whether it makes money?

Not whether it wins. AUC against `hit` is a trap: a -200 favourite wins more
often than a +150 dog by arithmetic, so the price scores 0.5446 on that metric
and means nothing. The question is whether sorting by a signal sorts by profit.

ROI by decile, 8,548 graded picks, 2023-2025:

    signal                    d1     d2     d3     d4     d5     d6     d7     d8     d9    d10   spread
    win probability        +4.1%  -4.3%  -2.0%  -8.0%  -1.2%  -4.6%  +0.2%  +4.3%  -2.0%  +3.5%    +0.9%
    expected value         -2.2%  -4.6%  -5.5%  -5.3%  -4.1%  +0.5%  +4.3%  +2.5%  +3.0%  +1.3%    +5.6%
    gap relative to line   +1.1%  -2.5%  -0.7%  -2.5%  -0.6%  +0.3%  -2.9%  -1.9%  -2.0%  +1.7%    +0.5%
    the price              +7.5%  -4.5%  +1.1%  -2.8%  -1.3%  -4.6%  -1.3%  -0.8%  -2.4%  -1.1%    -3.2%

Expected value is the only one of the four that ranks profit, and it is the one
the board selects on. Its bottom two deciles return -3.4% and its top two +2.2%,
a spread of 5.6 points.

Two things follow.

The gap to the line, which is what the gap-tier system ranked on from 27
September until it was reverted, has a spread of half a point. It does not rank
profit. That is the same conclusion the head-to-head reached from the other
direction, reached here without reference to any threshold.

And the win probability on its own barely ranks either, at +0.9%. It is only
useful once the price is subtracted from it, which is what expected value is.
So the lever for improving this product is not a better projection and not a
better probability in isolation: it is a probability that discriminates better
*against the price*, and that is what tune.py scores candidates on.
"""

import os, sys

import numpy as np, pandas as pd, stats_ci as S
from sqlalchemy import create_engine, text
import build_prop_edges as bp
pd.set_option("display.width",250)
e=create_engine(bp.DATABASE_URL, future=True)
d=pd.read_sql(text("""
  SELECT market_code, game_date, recommended_side, win_prob, expected_value,
         hit, price_american, line, projection_median
  FROM prop_edge_results
  WHERE hit IS NOT NULL AND win_prob IS NOT NULL AND source='backtest'
"""), e)
pay=np.where(d.price_american>0, d.price_american/100.0, 100.0/d.price_american.abs())
d["units"]=np.where(d.hit.astype(bool), pay, -1.0)
d["cluster"]=d.game_date.astype(str)
d["breakeven"]=1.0/(1.0+pay)
d["edge"]=d.win_prob-d.breakeven
d["gap_rel"]=(d.projection_median-d.line).abs()/d.line.abs().clip(lower=0.5)
print(f"{len(d):,} graded rows. ROI by decile of each candidate signal.\n")
def deciles(col, label):
    v=d[col].replace([np.inf,-np.inf],np.nan)
    m=v.notna()
    g=d[m].copy(); g["q"]=pd.qcut(v[m].rank(method="first"),10,labels=False)
    out=[]
    for i in range(10):
        s=g[g.q==i]
        out.append(s.units.mean())
    lo=g[g.q<=1]; hi=g[g.q>=8]
    w=hi.units.to_numpy()
    r,l,h=S.clustered_bootstrap(lambda i,w=w: float(np.mean(w[i])), hi.cluster.to_numpy(), n_boot=1500)
    print(f"  {label:<26}" + "".join(f"{x:>+7.1%}" for x in out))
    print(f"  {'':<26}top two deciles {r:>+6.1%} [{l:>+6.1%},{h:>+6.1%}]  "
          f"bottom two {lo.units.mean():>+6.1%}   spread {r-lo.units.mean():>+6.1%}")
print(f"  {'signal':<26}" + "".join(f"{'d'+str(i+1):>7}" for i in range(10)))
for col,label in (("win_prob","win probability"),
                  ("expected_value","expected value"),
                  ("edge","edge over break-even"),
                  ("gap_rel","gap relative to line"),
                  ("breakeven","the price (short to long)")):
    deciles(col,label)
