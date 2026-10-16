"""Archive-backed proof that two energy materials have no colour in any deferred or exec-masked program.

The Warmonger reticle and the Sheepinator internal energy are forward-only: every deferred writer and every
discard (exec-masked) variant of their graphs exports constant zero to the albedo/emission and F0/gloss targets,
so no deferred, frozen-input or discard preview can show them. See docs/material-decoding/forward-only-energy.md.
"""
import hashlib
import os
from pathlib import Path
import unittest

# graph path, graph SHA-256, {program index: (shader id, role)}
GRAPHS = (
    ('materialgraph/equipment/weapon/wpn_warmonger_reticle/wpn_warmonger_reticle.materialgraph',
     '78182cb38b81b861b1117bb9c4f36bbb0dc8f57e1d4593bae6a25e633d935ced',
     {0: '0x4c184dcc', 1: '0x11408de9', 2: '0x630cb83c', 3: '0x1e6c0141', 4: '0x11408de9', 5: '0x6c8f5a50'}),
    ('materialgraph/equipment/weapon/wpn_sheepinator_internal_energy/wpn_sheepinator_internal_energy.materialgraph',
     '697a8cc3ab623d51cf4f8c170c013f57616cfe97743c3805942d3dcdfe2fd3a7',
     {0: '0x46976f6e', 1: '0x11408de9', 2: '0x4fbb52c8', 3: '0x46976f6e', 4: '0x11408de9', 5: '0x4fbb52c8'}),
)
DEFERRED_OR_MASKED = (1, 2, 4, 5)
FORWARD_LOOPS = (0, 3)


class ForwardOnlyEnergyTests(unittest.TestCase):
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

    def programs(self, path, sha):
        from rc2016.levels.scene import asset_bytes
        from rc2016.shaders.translator import pixel_programs
        data = bytes(asset_bytes(self.archive, path))
        self.assertEqual(hashlib.sha256(data).hexdigest(), sha)
        return pixel_programs(data)

    def test_program_layout_is_exact(self):
        for path, sha, ids in GRAPHS:
            with self.subTest(graph=path):
                programs = self.programs(path, sha)
                self.assertEqual({k: p['shader_id'] for k, p in enumerate(programs)}, ids)

    def test_no_deferred_or_masked_program_carries_colour(self):
        from rc2016.shaders.translator import Compiler, decoder
        for path, sha, _ in GRAPHS:
            programs = self.programs(path, sha)
            for k in DEFERRED_OR_MASKED:
                with self.subTest(graph=path, program=k):
                    p = programs[k]
                    c = Compiler(p['code'], p['usage_slots'], p['ps_input_addr'], p['ps_input_ena'], p['vgpr_count'])
                    c.run(decoder().decode_stream(p['code'], 0))
                    # targets 1 (albedo, emission) and 2 (F0, gloss) are literal zeros in every lane
                    for target in (1, 2):
                        self.assertIn(target, c.exports)
                        for node in c.exports[target]:
                            if node is not None:
                                self.assertEqual(c.ir.value(node), 0.0)

    def test_visible_programs_are_single_target_forward_loops(self):
        from rc2016.shaders.translator import compile_graph
        from rc2016.levels.scene import asset_bytes
        for path, sha, _ in GRAPHS:
            with self.subTest(graph=path):
                graph = compile_graph(bytes(asset_bytes(self.archive, path)))
                for k in FORWARD_LOOPS:
                    program = graph['programs'][k]
                    self.assertEqual(program['status'], 'unsupported')
                    self.assertIn('forward-pass program: exports target 0', program['reason'])


if __name__ == '__main__':
    unittest.main()
