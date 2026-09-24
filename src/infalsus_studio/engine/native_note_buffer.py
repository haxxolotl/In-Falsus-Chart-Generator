"""Build-pinned side-note buffer contract for LogicalNotePlayer._pb."""
from collections import Counter
import struct
from .chart_emulator import Decoder
from unicorn.x86_const import UC_X86_REG_R14, UC_X86_REG_RSP, UC_X86_REG_RIP

SIDE_CAPACITY = 4096
TIMING_CAPACITY = 1024

def check(notes, timing_hex):
    counts = Counter(note['side'] for note in notes)
    assert max(counts.values(), default=0) <= SIDE_CAPACITY, (
        'native side-note buffer overflow', dict(counts), SIDE_CAPACITY)
    assert len(bytes.fromhex(timing_hex)) // 32 <= TIMING_CAPACITY, 'native timing buffer overflow'
    return dict(side_note_counts=dict(counts), side_capacity=SIDE_CAPACITY,
                timing_capacity=TIMING_CAPACITY)

def native_boundary_proof():
    """Execute the exact failing bounds branch, with the constructor's capacity."""
    decoder = Decoder(); uc = decoder.uc; base = decoder.base
    # _hA constructor: mov edx,0x1000, passed to IL2CPP array allocation.
    assert bytes(uc.mem_read(base + 0x53bd58, 5)) == b'\xba\x00\x10\x00\x00'
    # _pb loads side buffer count, compares to its Span length, then throws.
    assert bytes(uc.mem_read(base + 0x530ddc, 4)) == b'\x49\x63\x46\x28'
    results = []
    for index in [4095, 4096, 4507, 2988]:
        obj=0x100001000; stack=0x701ff000
        uc.mem_write(obj + 0x28, struct.pack('<i', index))
        uc.mem_write(stack + 0x30, struct.pack('<QI', 0x100010000, SIDE_CAPACITY))
        uc.reg_write(UC_X86_REG_R14, obj); uc.reg_write(UC_X86_REG_RSP, stack)
        uc.emu_start(base + 0x530ddc, base + 0x530def, count=4)
        target=uc.reg_read(UC_X86_REG_RIP)-base
        assert target == (0x53169f if index >= SIDE_CAPACITY else 0x530def), (index,hex(target))
        results.append(dict(index=index, branch_rva=hex(target), throws_index_out_of_range=index>=SIDE_CAPACITY))
    return dict(capacity=SIDE_CAPACITY, allocation_rva='0x53bd58', bounds_check_rva='0x530ddc', cases=results)
