"""The game seam (rc2016.games): folder detection, the 2016 adapter, capability gating in the window."""
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rc2016 import games
from rc2016.games import RC2016, RCRA
from rc2016.games.base import CACHE_ROOT, Game, GameUnavailable


def _rc2016_folder(root: Path) -> Path:
    """A minimal extracted R&C 2016 game folder: one archive root two levels down."""
    archive = root / 'CUSA01073' / 'Image0' / 'asset_archive'
    archive.mkdir(parents=True)
    for name in ('layout.csv', 'toc', 'dag', 'chunkmap.txt', 'a00s000'):
        (archive / name).write_bytes(b'\0' * 8)
    return root / 'CUSA01073'


def _rcra_folder(root: Path, magic=0x34E89035, dag=True, data_folder=True) -> Path:
    folder = root / 'Ratchet & Clank - Rift Apart'
    folder.mkdir(parents=True)
    (folder / 'toc').write_bytes(struct.pack('<II', magic, 0) + b'1TAD')
    if dag:
        (folder / 'dag').write_bytes(struct.pack('<I', 0xB8EF3955))
    if data_folder:
        (folder / 'd').mkdir()
    return folder


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # locate() remembers found roots in a sidecar; keep the test's out of the shared cache.
        sidecar = patch('rc2016.formats.archive.LOCATE_SIDECAR', self.root / 'located_roots.json')
        sidecar.start()
        self.addCleanup(sidecar.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_extracted_ps4_archive_is_rc2016(self):
        self.assertIs(games.detect(_rc2016_folder(self.root)), RC2016)

    def test_install_with_toc_magic_dag_and_d_is_rift_apart(self):
        self.assertIs(games.detect(_rcra_folder(self.root)), RCRA)

    def test_wrong_toc_magic_or_missing_parts_are_not_rift_apart(self):
        for number, kwargs in enumerate((dict(magic=0x77AF12AF), dict(dag=False), dict(data_folder=False))):
            folder = _rcra_folder(self.root / str(number), **kwargs)
            self.assertFalse(RCRA.detect(folder), kwargs)
            with self.assertRaises(ValueError):
                games.detect(folder)

    def test_unknown_folder_names_the_supported_games(self):
        (self.root / 'empty').mkdir()
        with self.assertRaises(ValueError) as caught:
            games.detect(self.root / 'empty')
        self.assertIn('R&C 2016', str(caught.exception))
        self.assertIn('Rift Apart', str(caught.exception))
        with self.assertRaises(ValueError):
            games.detect(self.root / 'missing')

    def test_rift_apart_without_its_reader_says_so(self):
        folder = _rcra_folder(self.root)
        with patch.dict(sys.modules, {'rc2016.games.rcra.source': None}):
            with self.assertRaisesRegex(GameUnavailable, 'Rift Apart reader not installed'):
                games.open_source(folder, 'source_preview', lambda *_a, **_k: None)

    def test_relative_output_is_under_the_game_cache(self):
        opened = []
        game = Game(id='fake', title='Fake', detect=lambda folder: True,
                    open_source=lambda folder, output, log: opened.append(output) or 'source')
        with patch('rc2016.games._DETECT_ORDER', (game,)):
            self.assertEqual(games.open_source(self.root, 'source_preview', None), (game, 'source'))
            games.open_source(self.root, self.root / 'absolute', None)
        self.assertEqual(opened, [CACHE_ROOT / 'fake' / 'source_preview', self.root / 'absolute'])


class GameRecordTests(unittest.TestCase):
    def test_rc2016_declares_everything_and_keeps_its_caches_in_place(self):
        self.assertEqual(RC2016.capabilities, games.CAPABILITIES)
        # The exact folder the loader and level viewer used before the seam, so nothing is rescanned.
        self.assertEqual(RC2016.cache_dir(), Path(__file__).resolve().parents[1] / '.cache')
        self.assertEqual(RCRA.cache_dir(), CACHE_ROOT / 'rcra')

    def test_rift_apart_milestone_one_capabilities(self):
        self.assertEqual(RCRA.capabilities, {'models', 'textures', 'export'})
        for capability in ('levels', 'flash', 'animation', 'pipeline', 'decoded_shading'):
            self.assertFalse(RCRA.has(capability), capability)
        self.assertEqual(RCRA.unavailable_tip, 'Not yet available for Rift Apart')
        self.assertEqual(RCRA.window_title, 'R&C Asset Browser — Rift Apart')

    def test_unknown_capability_or_hook_is_rejected(self):
        with self.assertRaises(ValueError):
            Game(id='x', title='X', detect=bool, open_source=print, capabilities=frozenset({'sound'}))
        with self.assertRaises(ValueError):
            Game(id='x', title='X', detect=bool, open_source=print, hooks={'play': print})
        with self.assertRaises(ValueError):
            RC2016.has('sound')

    def test_missing_hooks_raise_not_implemented(self):
        bare = Game(id='bare', title='Bare', detect=bool, open_source=print)
        with self.assertRaisesRegex(NotImplementedError, 'Bare'):
            bare.texture_header(None, 'a.texture')
        with self.assertRaisesRegex(NotImplementedError, 'Bare'):
            bare.list_models('folder', 'output')
        self.assertFalse(bare.shadow_only('a.material'))   # no hook: the game cannot tell, so nothing is left out
        with self.assertRaises(NotImplementedError):
            RC2016.list_models('folder', 'output')   # 2016 lists its models through the pipeline scan

    def test_default_output_is_beside_a_source_and_under_documents_for_an_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            programs, documents = Path(tmp) / 'Programs', Path(tmp) / 'Docs'
            source = Path(tmp) / 'Downloads' / 'CUSA01073'
            with patch.dict(os.environ, {'ProgramFiles': str(programs)}), \
                    patch('rc2016.games.base.documents_folder', return_value=documents):
                self.assertEqual(RC2016.default_output(source), source.parent / 'CUSA01073 Animation Output')
                install = programs / 'Steam' / 'steamapps' / 'common' / 'Ratchet & Clank - Rift Apart'
                self.assertEqual(RCRA.default_output(install), documents / 'R&C Asset Browser' / 'Rift Apart Output')
                library = Path(tmp) / 'SteamLibrary' / 'steamapps' / 'common' / 'Ratchet & Clank - Rift Apart'
                self.assertEqual(RCRA.default_output(library), documents / 'R&C Asset Browser' / 'Rift Apart Output')


class LoaderDispatchTests(unittest.TestCase):
    def test_decode_dispatches_through_the_game(self):
        from rc2016.viewer.loader import SourcePreviewLoader
        calls = []

        def decode(source, model_id, source_path, as_arrays):
            calls.append((source, model_id, source_path, as_arrays))
            return dict(meshes=[], joint_count=0), b'data'
        game = Game(id='fake', title='Fake', detect=bool, open_source=print, hooks={'decode_model': decode})
        loader = SourcePreviewLoader()
        self.assertIs(loader.game, RC2016)   # the default before any archive opens
        entry = loader._decode('archive', 'id', 'a.model', 'export', {}, game)
        self.assertEqual(calls, [('archive', 'id', 'a.model', False)])
        self.assertEqual(entry['data'], b'data')
        self.assertEqual(entry['decoded']['_archive_path'], 'a.model')

    def test_rift_apart_decode_goes_to_its_model_decoder(self):
        with patch('rc2016.games.rcra.model.decode_for_viewer', return_value=('decoded', b'data')) as decode:
            self.assertEqual(RCRA.decode_model('source', 'id', 'a.model', True), ('decoded', b'data'))
        decode.assert_called_once_with('source', 'id', 'a.model', True)

    def test_rift_apart_texture_and_material_hooks_reach_its_readers(self):
        for hook, module, name, args in (
                ('material_textures', 'material', 'material_textures', ('s', [], {}, None)),
                ('model_texture_list', 'material', 'model_texture_list', ('s', [])),
                ('material_rules', 'material', 'material_rules', ('s', [], None)),
                ('effect_preview', 'material', 'effect_preview', ('s', b'', 'a.model')),
                ('texture_header', 'texture', 'texture_header', ('s', 'a.texture')),
                ('load_texture', 'texture', 'load_texture', ('s', 'a.texture')),
                ('export_textures', 'texture', 'export_textures', ('s', ['a.texture'], 'out', {'dds'}))):
            with patch(f'rc2016.games.rcra.{module}.{name}', return_value='result') as reader:
                self.assertEqual(getattr(RCRA, hook)(*args), 'result', hook)
            reader.assert_called_once()
        rows = [dict(path='b/y.texture', folder='b'), dict(path='a/x.texture', folder='a')]
        with patch('rc2016.games.rcra.texture.archive_textures', return_value=rows):
            self.assertEqual(RCRA.texture_paths('s'), ['a/x.texture', 'b/y.texture'])

    def test_textures_tab_dispatches_through_the_game(self):
        from rc2016.viewer.loader import SourcePreviewLoader
        calls = []
        game = Game(id='fake', title='Fake', detect=bool, open_source=print, hooks=dict(
            texture_paths=lambda source: ['a/x_c.texture'],
            load_texture=lambda source, path, hd=False: calls.append(('load', path, hd)) or dict(
                path=path, width=1, height=1, format=28, pixels=b'\x10\x20\x30\x40', full_size=(1, 1), srgb=True),
            export_textures=lambda *args: calls.append(('export',) + args[1:]) or []))
        loader = SourcePreviewLoader()
        loader.archive, loader.game, loader.source_root = 'source', game, Path.cwd().resolve()
        self.assertEqual(loader.archive_textures(Path.cwd()),
                         [dict(path='a/x_c.texture', suffix='_c', materials=[], via_graph=False)])
        loader.export_textures(['a/x_c.texture'], 'out', {'dds'})
        self.assertEqual(calls, [('export', ['a/x_c.texture'], 'out', {'dds'}, False, None)])

    def test_rift_apart_export_maps_use_base_colour_and_normal_only(self):
        from rc2016.export.models import _MapCache
        from rc2016.games.rcra.material import SLOT_BASE_COLOUR, SLOT_G, SLOT_NORMAL
        textures = {
            'c.texture': dict(format=28, width=2, height=2, pixels=bytes([200, 100, 50, 255] * 4), srgb=False),
            'n.texture': dict(format=49, width=2, height=2, pixels=bytes([128, 128] * 4), srgb=False),
            'float_n.texture': dict(format=16, width=2, height=2, pixels=bytes(32), srgb=False)}
        slots = {'a.material': [(SLOT_BASE_COLOUR, 'c.texture'), (SLOT_NORMAL, 'n.texture'), (SLOT_G, 'g.texture')],
                 'b.material': [(SLOT_NORMAL, 'float_n.texture')]}
        source = SimpleNamespace(asset_data=lambda key: key)
        with patch('rc2016.games.rcra.material._material_key', side_effect=lambda s, path: path), \
                patch('rc2016.games.rcra.material.material_slots', side_effect=lambda data: slots[data]), \
                patch('rc2016.games.rcra.texture.load_texture_full', side_effect=lambda s, path: dict(textures[path])):
            a = RCRA.material_maps(source, dict(index=0, path='a.material'), {}, _MapCache())
            b = RCRA.material_maps(source, dict(index=1, path='b.material'), {}, _MapCache())
        self.assertEqual(a['sources'], {'_c': 'c.texture', '_n': 'n.texture'})   # _g is not decoded, so not read
        self.assertEqual(a['base'].shape, (2, 2, 3))
        self.assertEqual((a['alpha_mode'], a['double_sided'], a['emissive']), ('OPAQUE', False, None))
        self.assertTrue((a['mr'][..., 2] == 0).all() and (a['mr'][..., 1] == 255).all())   # non-metallic, fully rough
        self.assertIsNotNone(a['normal'])
        self.assertIn('Rift Apart preview', a['notes'][-1])
        self.assertEqual((b['sources'], b['normal'], b['base_factor']), ({}, None, [0.8, 0.8, 0.8]))
        self.assertTrue(any('float_n.texture left out' in note for note in b['notes']))

    def test_export_reads_materials_through_the_loaders_game(self):
        import inspect
        from rc2016.export import models
        source = inspect.getsource(models.ModelExporter.export)
        for call in ('game.material_maps(', 'game.export_textures(', 'game.shadow_only('):
            self.assertIn(call, source)
        with patch('rc2016.export.models.material_maps', return_value='maps') as maps:
            self.assertEqual(RC2016.material_maps('archive', {}, {}, None), 'maps')   # 2016 keeps its own function
        maps.assert_called_once_with('archive', {}, {}, None)

    def test_rift_apart_lists_its_models_in_process(self):
        closed = []
        source = SimpleNamespace(ids_of_type=lambda kind: {'model': ['k2', 'k1']}[kind],
                                 entries={'k1': dict(path='a/one.model'), 'k2': dict(path='b/two.model')},
                                 close=lambda: closed.append(True))
        with patch('rc2016.games.rcra_game.open_source', return_value=source):
            rows = RCRA.list_models('install', 'output')
        self.assertEqual([(r.model_id, r.archive_path) for r in rows], [('k1', 'a/one.model'), ('k2', 'b/two.model')])
        self.assertEqual(closed, [True])

    def test_levels_need_the_levels_capability(self):
        from rc2016.levels import scene
        with patch('rc2016.levels.scene.detect_game', return_value=RCRA):
            with self.assertRaisesRegex(ValueError, 'Levels are not yet available for Rift Apart'):
                scene.open_archive(tempfile.gettempdir())


class CapabilityGatingTests(unittest.TestCase):
    """AssetBrowserApp._gate/_apply_game on real Tk widgets (the full window needs OpenGL)."""

    def setUp(self):
        try:
            import tkinter as tk
            from tkinter import ttk
            self.root = tk.Tk()
            self.root.withdraw()
        except Exception as error:  # no Tk runtime or no display
            self.skipTest(f'Tk unavailable: {error}')
        from rc2016.app.window import ASSET_TYPE_CAPABILITY, HELP, AssetBrowserApp
        self.HELP = HELP
        shown = []
        app = SimpleNamespace(tk=tk, ttk=ttk, root=self.root, game=None, game_gates=[], options_changed=0,
                              asset_type_var=tk.StringVar(master=self.root, value='Level'),
                              shading_var=tk.StringVar(master=self.root, value='Generic reading'),
                              backend_var=tk.StringVar(master=self.root, value='Transform only'),
                              browser=SimpleNamespace(show_asset_type=shown.append))
        for name in ('_tip', '_gate', '_game_has', '_pipeline_backend', '_apply_game'):
            setattr(app, name, getattr(AssetBrowserApp, name).__get__(app))
        app._options_changed = lambda: setattr(app, 'options_changed', app.options_changed + 1)
        self.buttons = {kind: ttk.Radiobutton(self.root, text=kind) for kind in ASSET_TYPE_CAPABILITY}
        for kind, button in self.buttons.items():
            app._gate(ASSET_TYPE_CAPABILITY[kind], HELP['asset_type'], button)
        self.backend = ttk.Combobox(self.root, state='readonly')
        app._gate('pipeline', HELP['backend'], self.backend, 'readonly')
        self.animation = ttk.Checkbutton(self.root)
        app._gate('animation', HELP['animations'], self.animation, None)
        self.app, self.shown = app, shown

    def tearDown(self):
        self.root.destroy()

    def tips(self):
        return {id(tip.widget): tip.text for _cap, _widget, _state, tip, _text in self.app.game_gates}

    def test_everything_is_offered_before_a_game_is_recognised(self):
        self.assertTrue(self.app._game_has('levels'))
        self.assertFalse(self.app._pipeline_backend())   # Transform only, as chosen

    def test_rift_apart_disables_what_it_lacks_and_rc2016_restores_it(self):
        app = self.app
        app._apply_game(RCRA)
        self.assertEqual(self.root.title(), 'R&C Asset Browser — Rift Apart')
        tips = self.tips()
        for kind in ('Level', 'Flash UI'):
            self.assertTrue(self.buttons[kind].instate(['disabled']), kind)
            self.assertEqual(tips[id(self.buttons[kind])], 'Not yet available for Rift Apart')
        for kind in ('Model', 'Texture'):
            self.assertFalse(self.buttons[kind].instate(['disabled']), kind)
            self.assertEqual(tips[id(self.buttons[kind])], self.HELP['asset_type'])
        self.assertTrue(self.backend.instate(['disabled']))
        self.assertEqual(tips[id(self.animation)], 'Not yet available for Rift Apart')
        self.assertEqual((app.asset_type_var.get(), self.shown), ('Model', ['Model']))   # Level was showing
        self.assertEqual(app.shading_var.get(), 'All')
        self.assertTrue(app._pipeline_backend())   # its models come from its own index, not Transform only
        self.assertEqual(app.options_changed, 1)

        app._apply_game(RC2016)
        self.assertEqual(self.root.title(), 'R&C Asset Browser — R&C 2016')
        tips = self.tips()
        for button in self.buttons.values():
            self.assertFalse(button.instate(['disabled']))
            self.assertEqual(tips[id(button)], self.HELP['asset_type'])
        self.assertTrue(self.backend.instate(['readonly']) and not self.backend.instate(['disabled']))
        self.assertEqual(tips[id(self.animation)], self.HELP['animations'])
        self.assertFalse(app._pipeline_backend())


if __name__ == '__main__':
    unittest.main()
