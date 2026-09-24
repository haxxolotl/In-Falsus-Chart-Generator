"""Stage names, both jacket lookups and optional visual-only guides atomically."""
import copy
import hashlib
import json
from pathlib import Path
import struct
from . import installer as tx


def stage_extras(pack, game, job, song, charts, options):
    UnityPy, parse_binary = tx._runtime()
    manifest = tx._read_json(pack/tx.MANIFEST_NAME)
    aa = Path('infalsus_Data/StreamingAssets/aa')
    catalog_raw = (game/aa/'catalog.bin').read_bytes()
    catalog = parse_binary(catalog_raw,backend='python')
    warnings=[]
    baselines={e['path']:e['original_sha256'] for e in manifest['files']}
    baselines[(aa/'catalog.bin').as_posix()]=hashlib.sha256(catalog_raw).hexdigest()
    def write(relative, raw, kind):
        rel=Path(relative).as_posix(); dest=pack/'payload'/rel
        dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
        previous=next((r for r in manifest['files'] if r['path']==rel),None)
        original=baselines.get(rel,previous['original_sha256'] if previous else tx._sha256(game/rel))
        if tx._sha256(game/rel)!=original:
            raise tx.InstallError(f'Game data changed while preparing extras: {rel}')
        entry=dict(path=rel,kind=kind,bytes=len(raw),payload_sha256=tx._sha256(dest),
            original_sha256=original)
        manifest['files']=[r for r in manifest['files'] if r['path']!=rel]+[entry]
        manifest['protected_files'].pop(rel,None)
    def load(name, staged=False):
        dep=catalog.resources['release/'+name+'.asset'][0].dependencies[0].internal_id
        rel=aa/'StandaloneWindows64'/Path(dep).name
        path=pack/'payload'/rel if staged and (pack/'payload'/rel).exists() else game/rel
        raw=path.read_bytes()
        if not (staged and path==pack/'payload'/rel):baselines[rel.as_posix()]=hashlib.sha256(raw).hexdigest()
        env=UnityPy.load(raw);obj,data=tx._named_object(env,name)
        return rel,env,obj,data,tx._other_bytes(env,obj.path_id)
    def persist(name, loaded):
        rel,env,obj,data,other=loaded
        write(rel,tx._save_bundle(UnityPy,env,obj,data,name,other),'metadata')

    # Reading/sort overrides do not set displayed strings. Register actual
    # localized title and artist values without touching other song IDs.
    loaded=load('DynamicStringMapping');data=loaded[3]
    ident=manifest['category']['song_id']
    for field,value in [('songIdTitleTypeMapping',song['title']),('songIdArtistTypeMapping',song['artist'])]:
        mapping=data[field];ids=[v['Value'] for v in mapping['Ids']]
        values={language:value for language in mapping['IdValues'][0]}
        if ident in ids:
            i=ids.index(ident);mapping['IdStr'][i]=value;mapping['IdValues'][i]=values
        else:
            mapping['Ids'].append({'Value':ident});mapping['IdStr'].append(value);mapping['IdValues'].append(values)
        assert len(mapping['Ids'])==len(mapping['IdStr'])==len(mapping['IdValues'])
    persist('DynamicStringMapping',loaded)

    loaded=load('SongData',True);data=loaded[3]
    jacket_guid=None
    if options.get('cover'):
        from PIL import Image
        from UnityPy.export.Texture2DConverter import image_to_texture2d
        from .catalog_append import CatalogAppender
        cover=Path(options['cover']);source=Image.open(cover).convert('RGBA')
        source.thumbnail((2048,2048),Image.Resampling.LANCZOS)
        # Content addressing permits cover changes without replacing an in-use
        # dependency of some other song.
        jacket_guid=hashlib.sha256((song['base_name']+hashlib.sha256(source.tobytes()).hexdigest()).encode()).hexdigest()[:32]
        fallback=data['FallbackJacketLargeMaterial']['m_AssetGUID']
        dep=catalog.resources[fallback][0].dependencies[0].internal_id
        env=UnityPy.load(str(game/aa/'StandaloneWindows64'/Path(dep).name))
        asset=f'Assets/InFalsusStudio/{jacket_guid}.mat'
        pixels,fmt=image_to_texture2d(source,4)
        for obj in env.objects:
            if obj.type.name not in ('Texture2D','Material','AssetBundle'):continue
            d=obj.parse_as_dict()
            if obj.type.name=='Texture2D':
                d.update(m_Name=song['base_name'],m_Width=source.width,m_Height=source.height,
                    m_TextureFormat=int(fmt),m_MipCount=1,m_CompleteImageSize=len(pixels),m_IsReadable=False,
                    m_StreamData={'offset':0,'size':0,'path':''})
                d['image data']=pixels;d['m_TextureSettings']['m_FilterMode']=1
            elif obj.type.name=='Material':d['m_Name']=song['base_name']
            else:
                d['m_Name']=jacket_guid+'.bundle';d['m_AssetBundleName']=jacket_guid+'.bundle'
                d['m_Container'][0]=(asset,d['m_Container'][0][1])
            obj.save_typetree(d)
        old=next(k for k in env.file.files if not k.endswith('.resS'));cab=env.file.files[old]
        env.file.files={'CAB-'+jacket_guid:cab};cab.name='CAB-'+jacket_guid
        raw=env.file.save(packer='lz4');reopened=UnityPy.load(raw)
        tex=next(o for o in reopened.objects if o.type.name=='Texture2D').read().image.convert('RGBA')
        assert tex.size==source.size and tex.tobytes()==source.tobytes()
        assert asset in reopened.container
        write(aa/'StandaloneWindows64'/(jacket_guid+'.bundle'),raw,'jacket')
        if jacket_guid not in catalog.resources:
            appender=CatalogAppender(catalog_raw,fallback)
            appender.add(jacket_guid,asset,jacket_guid+'.bundle',jacket_guid,len(raw))
            changed=appender.finish();after=parse_binary(changed,backend='python')
            assert all(after.resources[k]==v for k,v in catalog.resources.items())
            write(aa/'catalog.bin',changed,'catalog')
            write(aa/'catalog.hash',hashlib.md5(changed).hexdigest().encode(),'catalog')
    def materials(key):
        large=copy.deepcopy(data['FallbackJacketLargeMaterial']);small=copy.deepcopy(data['FallbackJacketSmallMaterial'])
        if jacket_guid:large['m_AssetGUID']=small['m_AssetGUID']=jacket_guid
        return dict(key,JacketLargeMaterial=large,JacketSmallMaterial=small)
    songmats=data['songIdJacketMaterials']
    songmats[:]=[x for x in songmats if x['SongId']['Value']!=ident]+[materials({'SongId':{'Value':ident}})]
    # Exact chart lookup keys are used on the entry/gameplay screen.
    chartmats=data['chartIdJacketMaterials']
    names={c['name'].removesuffix('.spc') for c in song['charts']}
    chartmats[:]=[x for x in chartmats if x['ChartId'] not in names]+[materials({'ChartId':n}) for n in sorted(names)]
    persist('SongData',loaded)
    manifest['jacket']=dict(mode='custom' if jacket_guid else 'native-fallback',guid=jacket_guid,
        song_lookup=True,chart_lookups=4)

    if options.get('decorations'):
        from .engine.guide_lines_v21 import (parse_guides, bind_targets, source_group_motion,
                                             align_root_motion, map_lane_scene, ongeki_field_traces)
        records=[]
        for chart,meta in zip(charts,song['charts']):
            path=Path(meta.get('source_chart',''))
            scroll=[[r[1]*.001,r[3]] for r in struct.iter_unpack('<qiidff',bytes.fromhex(chart['timing_data_hex'])) if r[2]==0]
            if path.suffix.lower()=='.aff':
                arcs,groups,ignored,offset,scenes=parse_guides(path)
                if not arcs and not scenes:continue
                arcs,binding_receipt=bind_targets(arcs,meta['alignment'],chart['notes'])
                _,motion=source_group_motion(groups,meta['alignment'],song['duration_seconds'])
                motion=align_root_motion(motion,scroll)
            elif path.suffix.lower()=='.json' and 'ongeki' in str(path).lower():
                arcs=ongeki_field_traces(chart)
                if not arcs:continue
                motion=[dict(id=0,scroll=scroll,hidden=[],twoSided=False)]
                scenes=[]
            else:
                continue
            record=dict(name=meta['name'],guid=meta['guid'],hash=meta['sha256'],title=song['title'],
                duration=song['duration_seconds'],scroll=scroll,groups=motion,arcs=arcs)
            if scenes:record['laneScene']=map_lane_scene(scenes,meta['alignment'])
            records.append(record)
        if records:
            if not (game/'BepInEx/core/BepInEx.Unity.IL2CPP.dll').is_file():
                raise ValueError('Decorations need BepInEx 6 IL2CPP installed first. Install that loader, or turn Decorations off; ordinary charts need no loader.')
            relative=Path('BepInEx/plugins/SourceGuides/guides.json')
            guides=tx._read_json(game/relative) if (game/relative).exists() else dict(version=2,gameHash=tx.GAME_ASSEMBLY_SHA256,charts=[])
            if guides.get('gameHash')!=tx.GAME_ASSEMBLY_SHA256:raise ValueError('Existing decorations target a different game build.')
            replacing={r['name'] for r in records}
            guides['charts']=[r for r in guides['charts'] if r['name'] not in replacing]+records
            write(relative,json.dumps(guides,ensure_ascii=False,separators=(',',':')).encode(),'decorations')
            write(relative.parent/'SourceGuides.dll',(Path(__file__).parent/'assets/SourceGuides.dll').read_bytes(),'decorations')
            manifest['decorations']=dict(enabled=True,charts=len(records),arcs=sum(len(r['arcs']) for r in records))
        else:
            warnings.append('This input has no aligned source guide geometry; no decoration mod was installed.')
            manifest['decorations']=dict(enabled=False,reason='no aligned source guide geometry')
    tx._write_json(pack/tx.MANIFEST_NAME,manifest)
    return warnings
