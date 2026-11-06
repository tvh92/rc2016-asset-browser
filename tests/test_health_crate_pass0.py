"""Health (nanotech) crate: the pass-0 writer 5C03EF34 with its tangent normal, gated by exact hashes."""
import copy
import math
import os
from pathlib import Path
import random
import unittest

from rc2016.shaders.bindings import (
    HEALTH_CRATE_BODY_MATERIAL as BODY,
    HEALTH_CRATE_PANE_MATERIAL as PANE,
    HEALTH_CRATE_PASS0_SOURCE_PREVIEW as RULE,
    SOURCE_PREVIEW_POLICIES,
    VERTEX_TANGENT_FRAME,
    _preview_input_nodes,
    _source_preview_policy,
)
from rc2016.shaders.ir import DAG, compute

FAR_WRITER = ('0xe5f05839', '631f89a5a8bbdf88fbd741bac16eff6fb327e564de2a72edf344357e4e66b6ec')


class HealthCratePolicyTests(unittest.TestCase):
    def test_gate_admits_exactly_the_body_and_the_panes(self):
        program = {'shader_id': RULE['shader_id'], 'code_sha256': RULE['code_sha256']}
        self.assertIs(SOURCE_PREVIEW_POLICIES[RULE['graph_path']], RULE)
        for material in (BODY, PANE):
            found = _source_preview_policy([RULE['graph_path'].upper()], RULE['graph_sha256'], material.upper(),
                                           RULE['materials'][material], program)
            self.assertEqual((found['material_path'], found['material_sha256']), (material, RULE['materials'][material]))
        for paths, graph_sha, material, material_sha, shader in [
            (['required/materials/other.materialgraph'], RULE['graph_sha256'], BODY, RULE['materials'][BODY], program),
            ([RULE['graph_path']], '0' * 64, BODY, RULE['materials'][BODY], program),
            ([RULE['graph_path']], RULE['graph_sha256'], 'material/other/other.material', RULE['materials'][BODY], program),
            ([RULE['graph_path']], RULE['graph_sha256'], BODY, RULE['materials'][PANE], program),   # hashes are per material
            ([RULE['graph_path']], RULE['graph_sha256'], BODY, RULE['materials'][BODY],
             dict(shader_id=FAR_WRITER[0], code_sha256=FAR_WRITER[1])),
            ([RULE['graph_path']], RULE['graph_sha256'], BODY, RULE['materials'][BODY], dict(program, code_sha256='0' * 64)),
        ]:
            with self.subTest(graph=paths, material=material, shader=shader['shader_id']):
                self.assertIsNone(_source_preview_policy(paths, graph_sha, material, material_sha, shader))

    def test_frozen_inputs_give_the_close_range_normal(self):
        self.assertTrue(RULE['preferred'])
        self.assertEqual(RULE['normal'], 'tangent')
        self.assertEqual((RULE['input']['name'], RULE['input']['dword'], RULE['input']['value']), ('CB0[95]', 95, 1.0))
        words = {e['code_offset']: e['value'] for e in RULE['extra_inputs']}
        self.assertEqual(words, {404: -7.0 / 3.0, 396: 0.0})
        # fade = sat(rec[62] + POS_W rec[63]) = sat(-7/3) = 0, so s = 2 CB0[95] (1 - fade) = 2.
        self.assertEqual(max(0.0, min(1.0, words[404] + 50.0 * words[396])), 0.0)

    def test_typed_buffer_inputs_match_only_their_load(self):
        ir = DAG()
        load = dict(reason='TBUFFER_LOAD_FORMAT_X', buffer_access=dict(descriptor_proof=dict(api=1)))
        a = ir.node('unsupported', offset=404, **load)
        b = ir.node('unsupported', offset=396, **load)
        ir.node('unsupported', offset=404, reason='TBUFFER_LOAD_FORMAT_X', buffer_access=dict(descriptor_proof=dict(api=2)))
        cb = ir.node('input', name='CB0[95]', kind='cb', api=0, dword=95)
        self.assertEqual(_preview_input_nodes(ir, RULE['extra_inputs'][0]), [a])
        self.assertEqual(_preview_input_nodes(ir, RULE['extra_inputs'][1]), [b])
        self.assertEqual(_preview_input_nodes(ir, RULE['input']), [cb])

    def test_vertex_frame_normal_is_emitted_only_when_named(self):
        from rc2016.viewer.native_glsl import emit_surface
        ir = DAG()
        u = ir.node('input', name='attribute0.0', kind='attribute', attribute=0, channel=0)
        v = ir.node('input', name='attribute0.1', kind='attribute', attribute=0, channel=1)
        r = ir.node('texture', [u, v], api=7, sample=0, channel=0)
        g = ir.node('texture', [u, v], api=7, sample=0, channel=1)
        one = ir.const(1.0)
        translation = dict(status='ready', translation_sha256='0' * 64, nodes=ir.nodes,
                           texture_bindings=[dict(api=7, path='t/n.texture')],
                           outputs=dict(albedo=[one] * 3, F0=[one] * 3, gloss=one, emission=one),
                           normal=dict(api=7, sample=0, xyz=[r, g, one], frame=VERTEX_TANGENT_FRAME))
        self.assertIn('N = nativeVertexTangentNormal(N, nativeNormal);', emit_surface(translation)['body'])
        plain = dict(translation, normal=dict(translation['normal'], frame=None))
        del plain['normal']['frame']
        self.assertIn('N = nativeTangentNormal(N, nativeNormal);', emit_surface(plain)['body'])
        with self.assertRaises(ValueError):
            emit_surface(dict(translation, normal=dict(translation['normal'], frame='other')))


def _evaluate(nodes, roots, fixed, texels):
    """Values of DAG roots; fixed maps node index -> value (engine inputs), texels (api, channel) -> value."""
    values = dict(fixed)
    def visit(i):
        if i in values:
            return values[i]
        n = nodes[i]
        if n['op'] == 'const':
            value = n['value']
        elif n['op'] == 'input':
            value = (0.3, 0.6)[n['channel']] if n.get('kind') == 'attribute' and n.get('attribute') == 0 else None
            if value is None:
                raise KeyError(n['name'])
        elif n['op'] == 'texture':
            value = texels[(n['api'], n['channel'])]
        else:
            value = compute(n['op'], [visit(j) for j in n['args']])
        values[i] = value
        return value
    return [visit(i) for i in roots]


class HealthCrateArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        research = os.environ.get('RC2016_RESEARCH')
        if not research:
            raise unittest.SkipTest('RC2016_RESEARCH is required')
        root = Path(os.environ.get('RC2016_GAME_ROOT', str(Path(research).parents[1] / 'Ratchet & Clank (EUR)' / 'Image0')))
        if not root.exists():
            raise unittest.SkipTest('Source archive not available')
        from rc2016.levels.scene import open_archive
        cls.archive = open_archive(root)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'archive'):
            cls.archive.close()

    def _translation(self, material, context=True):
        from rc2016.formats.index import references
        from rc2016.formats.materials import extract_material_parameters
        from rc2016.levels.scene import asset_bytes
        from rc2016.shaders.bindings import prepare_translation
        graph = asset_bytes(self.archive, RULE['graph_path'])
        data = asset_bytes(self.archive, material)
        paths = [r['path'] for r in references(data) if r['path'].endswith('.materialgraph')]
        kwargs = dict(source_graph_paths=paths, source_material_path=material) if context else {}
        return prepare_translation([graph], data, extract_material_parameters(data), **kwargs)

    def test_unfrozen_writer_normal_is_the_decoded_closed_form(self):
        """5C03EF34: x, y = s (_n.rg - 0.5), z = 1 - x^2 - y^2, s = 2 CB0[95] (1 - sat(rec[62] + POS_W rec[63]))."""
        from rc2016.levels.scene import asset_bytes
        from rc2016.shaders.bindings import _lowered, compiled
        program = next(p for p in compiled(bytes(asset_bytes(self.archive, RULE['graph_path'])))['programs']
                       if p['shader_id'] == RULE['shader_id'])
        self.assertEqual(program['code_sha256'], RULE['code_sha256'])
        lowered = _lowered(program)
        nodes = lowered['nodes']
        self.assertEqual(lowered['normal']['api'], 7)
        engine = {}
        for i, n in enumerate(nodes):
            key = ('cb95' if n['op'] == 'input' and n.get('kind') == 'cb' and n.get('api') == 0 and n.get('dword') == 95 else
                   'pos_w' if n['op'] == 'input' and n.get('name') == 'ps.POS_W' else
                   n['offset'] if n['op'] == 'unsupported' and n.get('offset') in (396, 404) else None)
            if key is not None:
                engine.setdefault(key, []).append(i)
        self.assertEqual(sorted(map(str, engine)), ['396', '404', 'cb95', 'pos_w'])
        rng = random.Random(7)
        for _ in range(24):
            cb95, bias, slope, pos_w = rng.uniform(0.2, 1.5), rng.uniform(-3, 1), rng.uniform(-0.2, 0.2), rng.uniform(0, 40)
            r, g = rng.random(), rng.random()
            x, y, z = _evaluate(nodes, lowered['normal']['xyz'],
                                {i: value for key, value in (('cb95', cb95), (404, bias), (396, slope), ('pos_w', pos_w)) for i in engine[key]},
                                {(7, 0): r, (7, 1): g})
            s = 2 * cb95 * (1 - max(0.0, min(1.0, bias + pos_w * slope)))
            ex, ey = s * (r - 0.5), s * (g - 0.5)
            for got, want in ((x, ex), (y, ey), (z, 1 - ex * ex - ey * ey)):
                self.assertTrue(math.isclose(got, want, rel_tol=1e-9, abs_tol=1e-9), (got, want))

    def test_both_materials_bind_the_pass0_writer_and_keep_the_far_colour(self):
        from rc2016.viewer.native_glsl import evaluate_surface
        for material in (BODY, PANE):
            with self.subTest(material=material):
                near = self._translation(material)
                far = self._translation(material, context=False)
                self.assertEqual(near['status'], 'ready')
                self.assertEqual((near['shader_id'], near['code_sha256']), (RULE['shader_id'], RULE['code_sha256']))
                self.assertEqual((far['shader_id'], far['code_sha256']), FAR_WRITER)
                self.assertIsNone(far['normal'])
                excluded = [d for d in near['diagnostics'] if isinstance(d, dict) and d.get('excluded')]
                self.assertEqual([(e['shader_id'], e['code_sha256']) for e in excluded[0]['excluded']], [FAR_WRITER])
                self.assertEqual(near['normal']['frame'], VERTEX_TANGENT_FRAME)
                self.assertEqual(sorted(r['name'] for r in near['source_preview_inputs']),
                                 ['CB0[95]', 'instance rec[62]', 'instance rec[63]'])
                self.assertTrue(all(r['material_path'] == material for r in near['source_preview_inputs']))
                # Same colour terms as the far writer at random texels: only the normal differs.
                rng = random.Random(3)
                for _ in range(16):
                    texels = {(api, ch): rng.random() for api in (5, 6, 7, 8) for ch in range(4)}
                    sample = lambda api, uv: [texels[(api, ch)] for ch in range(4)]
                    a, b = evaluate_surface(near, (0.4, 0.7), sample), evaluate_surface(far, (0.4, 0.7), sample)
                    for key in ('albedo', 'F0'):
                        for p, q in zip(a[key], b[key]):
                            self.assertAlmostEqual(p, q, places=6)
                    for key in ('gloss', 'emission'):
                        self.assertAlmostEqual(a[key], b[key], places=6)
                    r, g = texels[(7, 0)], texels[(7, 1)]
                    x, y = 2 * r - 1, 2 * g - 1
                    length = math.sqrt(x * x + y * y + (1 - x * x - y * y) ** 2)
                    for got, want in zip(a['normal'], (x / length, y / length, (1 - x * x - y * y) / length)):
                        self.assertAlmostEqual(got, want, places=6)

    def test_validation_accepts_the_receipt_and_rejects_edits(self):
        from rc2016.shaders.validation import validate_health_crate_translation
        translation = self._translation(BODY)
        result = validate_health_crate_translation(translation)
        self.assertEqual(result['status'], 'passed_health_crate_pass0')
        self.assertLess(result['normal_max_difference'], 1e-6)
        edits = []
        edited = copy.deepcopy(translation); edited['source_preview_inputs'][0]['value'] = 0.5; edits.append(edited)
        edited = copy.deepcopy(translation); edited['normal']['frame'] = None; edits.append(edited)
        edited = copy.deepcopy(translation)
        next(b for b in edited['texture_bindings'] if b['api'] == 7)['slot_key'] = 0; edits.append(edited)
        edited = copy.deepcopy(translation)
        for row in edited['source_preview_inputs']:
            row['material_path'] = 'material/other/other.material'
        edits.append(edited)
        for edited in edits:
            with self.assertRaises((AssertionError, ValueError)):
                validate_health_crate_translation(edited)

    def test_viewer_program_uses_the_vertex_frame(self):
        from rc2016.viewer.native_glsl import fragment_source
        translation = self._translation(PANE)
        source = fragment_source(translation, alpha_api=5, alpha_discard=4.0 / 255.0)['source']
        self.assertIn('N = nativeVertexTangentNormal(N, nativeNormal);', source)
        self.assertIn('uNativeTex7', source)
        self.assertIn('paneAlpha = clamp(texture2D(uNativeTex5, vUV).a, 0.0, 1.0)', source)


if __name__ == '__main__':
    unittest.main()
