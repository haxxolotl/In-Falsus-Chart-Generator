from infalsus_studio.engine import effect_translation_v20 as effects


def test_25ms_source_flash_keeps_distance_with_readable_scroll(tmp_path, monkeypatch):
    source = tmp_path / 'song.aff'
    source.write_text('\n'.join((
        'AudioOffset:0', '-', 'timing(0,155.00,4.00);',
        'timing(1000,19840.00,16.00);',
        'timing(1025,0.01,16.00);',
        'timing(3000,155.00,4.00);',
    )), encoding='utf-8')
    monkeypatch.setattr(effects, 'ROOTS', (tmp_path,))
    _, report = effects.planned({
        'source_chart': source.name, 'level': 3,
        'alignment': {'time_scale': 1.0, 'offset_seconds': 0.0},
    }, 5000)
    pulse, = report['expanded_impulses']
    assert pulse['source_span_ms'] == 25
    assert pulse['replacement_speed'] == 4.0
    assert pulse['replacement_end'] == 1800
    assert pulse['signed_distance_seconds'] == 3.2
    assert report['withheld_impulses'] == []
