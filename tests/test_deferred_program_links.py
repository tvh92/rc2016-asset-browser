"""Level surface programs link in the material pump, never all at once inside a draw."""
import unittest

from rc2016.levels.viewport import LevelViewport


class FakeShading:
    def __init__(self, fail=()):
        self.programs, self.linked, self.fail = {}, [], set(fail)

    def native_program_key(self, translation, srgb, instanced=False, scene=False, experimental=False):
        return (translation['translation_sha256'], tuple(sorted(srgb.items())), instanced, scene, experimental)

    def native_program(self, translation, srgb, instanced=False, scene=False, experimental=False):
        key = self.native_program_key(translation, srgb, instanced, scene, experimental)
        if translation['translation_sha256'] in self.fail:
            raise RuntimeError('link failed')
        self.linked.append(key)
        self.programs[key] = (len(self.linked), {})
        return self.programs[key]


def entry(name, native=True, preview=True):
    out = {}
    if native:
        out.update(native_translation=dict(translation_sha256='native-' + name), native_textures={5: (1, True)})
    if preview:
        out.update(experimental_preview=dict(translation_sha256='preview-' + name), experimental_textures={5: (2, False)})
    return out


def level_view(shading, materials):
    view = object.__new__(LevelViewport)
    view._shading, view._mode, view.native_translation = shading, 'Shaded', True
    view.experimental_materials, view.lighting_enabled, view._scene_effects = True, True, None
    view._materials, view._draw_list, view._material_pump = materials, None, None
    view.scheduled, view.draws = [], 0
    view.after = lambda ms, function: view.scheduled.append(function) or 'after'
    view._make_current = lambda: True
    view._schedule_draw = lambda: setattr(view, 'draws', view.draws + 1)
    return view


def selected(view, name):
    return view._selected_surface(view._materials[name], 2)[0]


class DeferredProgramLinkTests(unittest.TestCase):
    def test_unlinked_preview_draws_its_native_surface_until_the_pump_links_it(self):
        shading = FakeShading()
        view = level_view(shading, {'a': entry('a')})
        shading.native_program(dict(translation_sha256='native-a'), {5: True}, True, False, False)   # linked at load
        self.assertEqual(selected(view, 'a')['translation_sha256'], 'native-a')
        self.assertEqual(view.scheduled, [view._pump_materials])
        view._pump_materials()
        self.assertEqual(selected(view, 'a')['translation_sha256'], 'preview-a')
        self.assertEqual(view.draws, 1)

    def test_nothing_linked_falls_back_to_the_plain_shader_and_queues_only_the_wanted_program(self):
        shading = FakeShading()
        view = level_view(shading, {'a': entry('a')})
        self.assertIsNone(selected(view, 'a'))
        self.assertEqual([args[0]['translation_sha256'] for args in view._program_links.values()], ['preview-a'])
        view.experimental_materials = False
        self.assertIsNone(selected(view, 'a'))
        view._pump_materials()
        self.assertEqual({key[0] for key in shading.linked}, {'preview-a', 'native-a'})

    def test_the_pump_spreads_links_over_ticks(self):
        shading = FakeShading()
        view = level_view(shading, {name: entry(name) for name in 'abcd'})
        for name in 'abcd':
            selected(view, name)
        view._pump_materials(budget=0.0)   # at least one link per tick
        self.assertEqual(len(shading.linked), 1)
        self.assertEqual(view.scheduled[-1], view._pump_materials)
        view._pump_materials(budget=float('inf'))
        self.assertEqual(len(shading.linked), 4)
        self.assertEqual(view._pending_links(), [])

    def test_a_failed_link_is_selected_so_the_draw_reports_it(self):
        shading = FakeShading(fail={'preview-a'})
        view = level_view(shading, {'a': entry('a', native=False)})
        self.assertIsNone(selected(view, 'a'))
        view._pump_materials()
        self.assertEqual(selected(view, 'a')['translation_sha256'], 'preview-a')
        self.assertEqual(view._pending_links(), [])

    def test_precompile_queues_previews_too(self):
        shading = FakeShading()
        view = level_view(shading, {'a': entry('a', native=False)})
        view._precompile('a')
        self.assertEqual(len(view._pending_links()), 1)


if __name__ == '__main__':
    unittest.main()
