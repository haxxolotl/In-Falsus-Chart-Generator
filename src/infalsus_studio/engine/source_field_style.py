"""Source height controls contour width; native coordinate repair is separate."""
# 25th--85th width percentiles from all 280 official charts, by difficulty.
WIDTHS = ((1/3, 1/2), (1/3, 1/2), (1/4, 5/9), (2/9, 7/12))

def arc_width(x, height, level, minimum=0.0):
    low, high = WIDTHS[level]
    width = low + (high-low)*(1-min(1.0, max(0.0, float(height))))
    # A broad middle contour tapers at the sides instead of spilling off-field.
    width = min(width, 2*max(0.0, min(float(x), 1-float(x))))
    # Existing accepted cursor positions remain playable during migration.
    return max(minimum, .14, width)
