"""Bind the proven chart algorithms to one isolated job and local calibration."""
import importlib
from pathlib import Path
import sys


def configure(models, job):
    models, job = Path(models), Path(job)
    names = ('generate_arranged_charts', 'convert_source_charts', 'prepare_revision20',
             'prepare_revision21', 'guide_lines_v21', 'native_note_buffer', 'source_charts',
             'fix_v10_loading_buffers', 'cursor_continuity')
    for name in names:
        importlib.import_module('.' + name, __package__)
    for name, module in list(sys.modules.items()):
        if name.startswith(__package__ + '.'):
            for key in ('B', 'BASE'):
                if hasattr(module, key): setattr(module, key, models)
            if hasattr(module, 'ROOT'): module.ROOT = models / 'revision2'
    from . import generate_arranged_charts as gen, convert_source_charts as convert
    from . import prepare_revision20 as v20, prepare_revision21 as v21
    gen.R = models / 'revision4'
    convert.R = job
    v20.OLD = job / 'original'
    v21.OLD = job / 'corrected'
    return gen, convert, v20, v21
