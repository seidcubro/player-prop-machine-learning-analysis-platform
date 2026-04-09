from pathlib import Path

path = Path(r".\services\api\app\routes\jobs.py")
text = path.read_text(encoding="utf-8-sig")

text = text.replace(
    """            recs_mean,
            recs_trend,""",
    """            aux_mean,
            aux_trend,"""
)

text = text.replace(
    """            :recs_mean,
            :recs_trend,""",
    """            :aux_mean,
            :aux_trend,"""
)

text = text.replace(
    """          recs_mean = EXCLUDED.recs_mean,
          recs_trend = EXCLUDED.recs_trend,""",
    """          aux_mean = EXCLUDED.aux_mean,
          aux_trend = EXCLUDED.aux_trend,"""
)

text = text.replace(
    """"recs_mean": aux_mean,
                    "recs_trend": aux_trend,""",
    """"aux_mean": aux_mean,
                    "aux_trend": aux_trend,"""
)

path.write_text(text, encoding="utf-8")
print("Patched jobs.py to use aux_mean/aux_trend")
