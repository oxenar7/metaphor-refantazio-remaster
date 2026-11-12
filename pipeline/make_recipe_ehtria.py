"""Write ehtria/recipe_ehtria.json: ground (prebuilt from the terrain jobs) + wood materials."""
import sys, os, json, hashlib, glob
S = sys.argv[1]; W = lambda p: os.path.join(S, p)
pre = {}
for d in ['ehtria/allx/COMMON/model/field/object/o_0001_132_00.TEX', 'ehtria/ext/COMMON/model/field/object/o_0004_550_00.TEX']:
    for f in sorted(glob.glob(W(d + '/replaced/*.dds'))):
        b = os.path.basename(f); name = b.split('__', 1)[1][:-4]
        src = open(W(d + '/' + b), 'rb').read()
        pre[name] = {'dds': f, 'src_sha': hashlib.sha1(src).hexdigest()[:12]}
st = lambda p, own, **k: dict(mode='stat', photo=p, own=own, **k)
de = lambda p, **k: dict(mode='detail', photo=p, **k)
nr = lambda p, s=1.0, **k: dict(mode='nreplace', photo=p, strength=s, **k)
nc = lambda p, s=0.8, **k: dict(mode='ncombine', photo=p, strength=s, **k)
m = {
 # pass 6: Eht Ria sun is a strong warm gain (~2.2, 2.15, 1.36 linear), so grey photo wood read pale yellow and spotty.
 # Woods now get an explicit warm-brown pre-light mean ("lab"), keep the photo grain at moderate contrast, softer normals.
 'bg82_paganIsland_wood_01_m1_basecolor.tga': st('rough_wood', 0.5, lab=[38, 8, 15]),
 'bg82_paganIsland_wood_01_m1_normal.tga': nr('rough_wood', 1.0),
 'bg82_paganIsland_woodPlanks_01_m1_basecolor.tga': st('weathered_planks', 0.6, lab=[42, 6, 15]),
 'bg82_paganIsland_woodPlanks_01_m1_normal.tga': nr('weathered_planks', 1.1),
 'bg82_paganIsland_Pier_wood_m1_basecolor.tga': st('rough_wood', 0.5, lab=[37, 6, 12]),
 'bg82_paganIsland_Pier_wood_m1_normal.tga': nr('rough_wood', 1.0),
 'bg82_paganIsland_MeatShop_wood_01_m1_basecolor.tga': st('rough_wood', 0.5, lab=[40, 6, 12]),
 'bg82_paganIsland_woodPlanksMeatShop_01_m1_basecolor.tga': st('weathered_planks', 0.6, lab=[38, 5, 11]),
 'bg04_Old_Wood_Siding_01_basecolor.tga': st('weathered_brown_planks', 0.7, gain_l=1.1),
 'bg04_Old_Wood_Siding_01_normal.tga': nr('weathered_brown_planks', 1.2),
 # layouts / painted details (moss on the blend woods) kept, finer and softer real grain on top
 'bg82_paganIsland_wood_Blend_01_m1_basecolor.tga': de('rough_wood', detail=0.6, lab=[35, 3, 17]),
 'bg82_paganIsland_wood_Blend_01_m1_normal.tga': nc('rough_wood', 0.7),
 'bg82_paganIsland_Pier_wood_Blend_01_m1_basecolor.tga': de('rough_wood', detail=0.7, lab=[35, 3, 13]),
 'bg82_woodLadder_01_m1_basecolor.tga': de('rough_wood', detail=0.7),
 'bg82_woodLadder_01_m1_normal.tga': nc('rough_wood', 0.8),
 'bg82_paganIsland_Weaponshop_wood_01_m1_basecolor.tga': de('rough_wood', detail=0.8),
 'bg82_paganIsland_Weaponshop_wood_01_m1_normal.tga': nc('rough_wood', 0.9),
 'bg82_paganIsland_PaintWood_01_m1_basecolor.tga': de('rough_wood', detail=0.8),
 'bg06_d6_sewer_lumberSet_01_m1_basecolor.tga': de('rough_wood', detail=0.7),
 'bg06_d6_sewer_lumberSet_01_m1_normal.tga': nc('rough_wood', 0.8),
 'bg06_d6_sewer_lumberSet_01_m2_basecolor.tga': de('rough_wood', detail=0.8, own=0.3, rot=90, lab=[40, 6, 16]),
 'bg06_d6_sewer_lumberSet_01_m2_normal.tga': nc('rough_wood', 0.9, rot=90),
 'bg04_paganIsland_woodFence_01_basecolor.tga': de('rough_wood', detail=0.8, own=0.3, lab=[41, 5, 17]),
 'bg04_paganIsland_woodFence_01_normal.tga': nc('rough_wood', 0.9),
 'bg04_paganIsland_seaWood_01_m1_basecolor.tga': de('rough_wood', detail=0.9, own=0.5),
 'bg04_paganIsland_seaWood_01_m1_normal.tga': nc('rough_wood', 1.0),
 # pass 6: tree trunks (ribbon-wrapped trees), 256 -> 1024 with bark-like grain; flat painted moss patches -> photo moss
 'bg04_paganIsland_tree_trunk_01_m1_basecolor.tga': de('rough_wood', detail=0.6),
 'bg04_paganIsland_tree_trunk_01_m1_normal.tga': nc('rough_wood', 0.7),
 'bg04_paganIsland_tree_trunk_02_m1_basecolor.tga': de('rough_wood', detail=0.6),
 'bg04_paganIsland_tree_trunk_02_m1_normal.tga': nc('rough_wood', 0.7),
 'bg04_forestroadtree_tropical_rockmoss_m1_basecolor.tga': st('leafy_grass', 0.3, scale=2, lab=[27, -12, 22]),
 'bg04_forestroadtree_tropical_rockmoss_m1_normal.tga': nr('leafy_grass', 0.8, scale=2),
 'bg82_jungleMoss_01_m1_basecolor.tga': st('forest_leaves_02', 0.3, gain_l=1.05),
 'bg82_jungleMoss_01_m1_normal.tga': nr('forest_leaves_02', 0.8),
}
for n in m: pre.pop(n, None)
R = {'cpk': r'C:\Program Files (x86)\Steam\steamapps\common\METAPHOR\base.cpk', 'toc': W('cpk_toc.tsv'), 'detail': W('tex_detail.tsv'),
     'texconv': W('tools/bin/texconv.exe'), 'photos': W('ehtria/ph/dl'), 'work': W('ehtria/rm_work'),
     'out': os.path.join(os.environ['LOCALAPPDATA'], 'Programs', 'Reloaded-II', 'Mods', 'metaphor.remaster.terrain', 'MFEssentials', 'CPK', 'Remaster'),
     'scope': ['COMMON/model/field/object'], 'prebuilt': pre, 'materials': m,
     'upscaled_map': W('upall/upscaled_map.json')}  # AI upscale (upscale_all.py) for the entries not replaced here
os.makedirs(R['work'], exist_ok=True)
json.dump(R, open(W('ehtria/recipe_ehtria.json'), 'w'), indent=1)
# how many recipe names also live outside the scope (left untouched for now)
outside = {}
for line in list(open(W('tex_detail.tsv'), encoding='utf-8'))[1:]:
    c = line.rstrip('\n').split('\t')
    if (c[2] in m or c[2] in pre) and c[0].startswith('COMMON') and not c[0].startswith('COMMON/model/field/object'):
        outside.setdefault(c[0], set()).add(c[1])
print('prebuilt', len(pre), 'materials', len(m))
print('outside scope:', {k: len(v) for k, v in outside.items()})
