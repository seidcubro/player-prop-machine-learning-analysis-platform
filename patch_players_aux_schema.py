from pathlib import Path

path = Path(r".\services\api\app\routes\players.py")
text = path.read_text(encoding="utf-8-sig")

text = text.replace(
    """              COALESCE(recs_mean, 0.0) AS recs_mean,
              COALESCE(recs_trend, 0.0) AS recs_trend,""",
    """              COALESCE(aux_mean, COALESCE(recs_mean, 0.0)) AS aux_mean,
              COALESCE(aux_trend, COALESCE(recs_trend, 0.0)) AS aux_trend,"""
)

text = text.replace(
    """"recs_mean": float(feat["recs_mean"] or 0.0),
        "recs_trend": float(feat["recs_trend"] or 0.0),""",
    """"aux_mean": float(feat["aux_mean"] or 0.0),
        "aux_trend": float(feat["aux_trend"] or 0.0),
        "recs_mean": float(feat["aux_mean"] or 0.0),
        "recs_trend": float(feat["aux_trend"] or 0.0),"""
)

path.write_text(text, encoding="utf-8")
print("Patched players.py to use aux_mean/aux_trend with backward compatibility")
