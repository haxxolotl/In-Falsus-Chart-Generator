from pathlib import Path
from .source_scroll_effects_v14 import read,save
from .cursor_motion_v17 import apply as limit_motion
from .cursor_paths_v14 import audit
from .arrangement_metrics import extended
from .flick_zone_v26 import repair as repair_flick_zone
import pathlib
B=pathlib.Path(__file__).parent
R=B/'revision17'
def cover_flicks(notes):
 return len(repair_flick_zone(notes))
