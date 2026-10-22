"""Graphs whose lowered deferred writers read one grey F0 from `_g.G` and the gloss from `_g.R`, with no GRAPH_RULES entry.

The generic reading takes `_g` as coloured F0 (rgb) and gloss (alpha), which tints these surfaces. Each entry is an exact
graph path with the SHA-256 of its bytes. The layout is derived from the writers' own export expressions
(rc2016/shaders/g_layout.py); `python -m rc2016.shaders.g_layout` lists the candidates and tests/test_grey_g_graphs.py
recomputes every entry. Graphs with a material whose native translation is ready are not listed, since native
translation replaces a family fallback.
"""
GREY_G_GRAPHS = {
    'materialgraph/character/hero/hero_clank_eyes/hero_clank_eyes.materialgraph':
        '1face496e9ffa35dbb71b7bc6307991e3950cc2403c192033b959038de900a5a',
    'materialgraph/environment/batalia/rock/bat_rock_snow_blend_rock_01/rb_bat_rock_snow_blend_rock_01.materialgraph':
        'e100811722eab8ef2c3b9b5e40edf31289ff1d53a9672eea2cb1532b4a76d172',
    'materialgraph/environment/deplanetizer/architecture/dep_arch_glass_panels_01/dep_arch_glass_panels_01.materialgraph':
        '5cf25d6c0899f68a5e5a2b663e3d3ea56dc9d10731df0af1355d29afe0b0b62c',
    'materialgraph/environment/deplanetizer/architecture/dep_arch_hangar_metal_nows_01/dep_arch_hangar_metal_nows_01.materialgraph':
        '0a0f22323ab5ab19511271956b50a1f6c275ec4f188dee654c441e0fb3436da0',
    'materialgraph/environment/deplanetizer/ground/dep_gnd_plain_metal_01/dep_gnd_plain_metal_01.materialgraph':
        'e32b777f5cce0f4d7affdf83990d8a7e4e7661a8332ccaf9103974c6b6c8fa72',
    'materialgraph/environment/deplanetizer/ground/dep_gnd_world_space_stains_01/dep_gnd_world_space_stains_01.materialgraph':
        '1694d9c41a6bf4618b3dc3152cf78debd8ffa85276e0c1abbd195ab3aebd464b',
    'materialgraph/environment/global/decal/mo_decal_gnormal.materialgraph':
        '7849348e0e69b686b28d581f271ec3441a4e914fb486243b7cdbd564462c31a8',
    'materialgraph/environment/global/prop/gbl_prop_blarg_mercenary_bomb_01/gbl_prop_blarg_mercenary_bomb_01.materialgraph':
        '7b54c00bc1cd3ff9f43a90b87104d90387bc141842698cc41339157a9aa6b498',
    'materialgraph/environment/global/prop/gbl_prop_museum_teleport_pad_01/gbl_prop_museum_teleport_pad_01.materialgraph':
        'bc1974d6e18c1cb11f41d4dd3398937e84fec97f53f8baf2ec99b718384d9664',
    'materialgraph/environment/global/rock/gbl_rock_ds_sediment_01/ari_rock_ds_sediment_01.materialgraph':
        '4a8ba721d85c48b544b7248a562ed6b2d2b195540fc30e82ae184add71edb99d',
    'materialgraph/environment/metropolis/ambient/met_amb_water_pool_01/rb_ari_amb_quick_sand_01.materialgraph':
        'b8282b409880a81f9f050c124f06c028925f3587116e0c2b45fe35f14f688d8c',
    'materialgraph/environment/metropolis/ground/met_gnd_grass_01/met_gnd_grass_01.materialgraph':
        '7468079c3c4b4944693586db4451735984f7991c3c18b2fea094ead71092e2ef',
    'materialgraph/environment/metropolis/plant/met_plant_grass_01/met_plant_grass_01.materialgraph':
        'e1ac66b18d6135eadde30b1168d92e78d8d40046a21dc769913b126864f23f2b',
    'materialgraph/environment/metropolis/prop/met_prop_sign_floating_01/met_prop_sign_floating_01.materialgraph':
        'afa1976c0b226a15d99476f7f70d0c5622d9f00bb903562c7c3223089cd3ec88',
    'materialgraph/environment/quartu/architecture/qrt_arch_broken_cement_01/bat_arch_borken_cement_02.materialgraph':
        '4fe06748d2b3ea3d21ab155b67772acca32e0b350694767c40470642730fa3ae',
    'materialgraph/environment/quartu/architecture/qrt_arch_broken_cement_01/qrt_arch_broken_cement_01.materialgraph':
        '906809970bb2ab31ea3c589d90673431e9f459a2c43380a3dc8cae48d5c4e190',
    'materialgraph/environment/veldin/plant/vel_plant_tree_gradient_01/vel_plant_tree_gradient_01.materialgraph':
        'd5d3556ca0da71bb824ce76cd00b0bb77419c5397aefa2ef650c56e1d12af3dc',
    'materialgraph/environment/veldin/test/test_vel_metal_blend_01/ja_test_vel_metal_blend_01.materialgraph':
        '0386eb681ed34934ff092714b36cf72bb930cbf1274b5a88ad06ca0a7026e2a7',
    'materialgraph/global/basic_foliage_split_face_texture/basic_foliage_split_face_texture.materialgraph':
        '5f021744d49320010db474579ab11c0d9ca78bbcfbcefa2deb0d2e19a30d532d',
    'materialgraph/global/basic_parallax_occlusion/gbl_prop_turbine_01.materialgraph':
        'f0737eeaa30322213288c0baefdf038786763732b334636da59cdfe1d84d77c5',
    'materialgraph/global/basic_parallax_occlusion/jn_scrolling_uvs_parallax_occlusion.materialgraph':
        '8a53b068c0572f06050037f59d0cb90c15b3d790eb5e873c91c22ab8600630f1',
    'materialgraph/global/basic_parallax_occlusion/parallax_occlusion_emissive.materialgraph':
        '570ea4b33f5b5f1f404e6ded755d82e54638a0374a6b5d3d89d92684fff55111',
    'materialgraph/global/basic_uv_scroll_01/jn_scrolling_uvs.materialgraph':
        '684062a9c669e01d85b41c6b60ddf9f35c0531300a1b9a5a3dff241d8b8cede6',
    'materialgraph/global/glb_default_grid_scrolling/glb_default_grid_scrolling.materialgraph':
        'f248e051a0100723f625cf5873e1b054844790acc24fd79f1ef29d5811c40f4d',
    'materialgraph/global/rcps4_ammo_containers/rcps4_ammo_containers.materialgraph':
        '8afa58e1c2947cfefb65dc81800dd0fec702defede89eafde0c6de9edca4583c',
    'materialtemplates/gl_worldspace_uvs/rv_worldspace_uvs_basic.materialgraph':
        'f867c0876248203292e581dcfab94f74e8af99484f05bedb73ae6a65b228a9c2',
    'materialtemplates/gl_worldspace_uvs/worldspace_uvs_basic.materialgraph':
        '1308700ef7ce2b7df8f06ae64d5301702778b13fe9578cde5ab52aac34eb5393',
    'materialtemplates/gl_worldspace_uvs_3axis/gl_worldspace_uvs_3axis_fresnel.materialgraph':
        '535ff454ddedef207c2084191eeae97c398709d116e62409cbf3291456c71f74',
}
