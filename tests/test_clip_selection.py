"""Exact clip selection: label selectors, the browser picker's selectors, and the source audit trail."""
import unittest

from rc2016.pipeline.clip_selection import ClipSelectionError, resolve_clip_selection


def source_row(order, name, stem, status='included', raw='aa', name_hash='0x1'):
    return dict(order=order, name=name, name_hash=name_hash, raw_sha256=raw,
                animset_path=f'character/x/{stem}.animset', status=status)


class LabelSelectors(unittest.TestCase):
    def setUp(self):
        self.entries = [source_row(0, 'Idle', 'hero'), source_row(1, 'Idle', 'npc', raw='bb', name_hash='0x2')]

    def test_label_with_an_animset_stem_selects_only_that_animset(self):
        selected, catalog = resolve_clip_selection(self.entries, ['label:hero: Idle'])
        self.assertEqual([row['order'] for row in selected], [0])
        self.assertEqual([row['selection_status'] for row in catalog], ['included', 'omitted_by_user_selection'])

    def test_plain_name_selector_is_ambiguous_when_two_animsets_hold_the_clip(self):
        with self.assertRaisesRegex(ClipSelectionError, 'Ambiguous'):
            resolve_clip_selection(self.entries, ['name:Idle'])

    def test_label_without_a_stem_matches_by_name(self):
        entries = [source_row(0, 'Idle', 'hero'), source_row(1, 'Walk', 'npc', raw='bb')]
        selected, _catalog = resolve_clip_selection(entries, ['label:Idle'])
        self.assertEqual([row['order'] for row in selected], [0])


class PickedSelectors(unittest.TestCase):
    def test_a_selector_matching_only_an_unsupported_row_is_skipped_and_listed(self):
        entries = [source_row(0, 'Idle', 'hero', status='unsupported'), source_row(1, 'Walk', 'npc', raw='bb')]
        selected, catalog = resolve_clip_selection(entries, ['name:Idle'], picked=True)
        self.assertEqual(selected, [])
        self.assertEqual(catalog[0]['selection_provenance']['skipped_selectors'],
                         [dict(selector='name:Idle', reason='matches no supported source clip')])

    def test_a_selector_matching_no_row_is_skipped_and_the_rest_still_select(self):
        entries = [source_row(0, 'Walk', 'npc', raw='bb'), source_row(1, 'Idle', 'hero')]
        selected, catalog = resolve_clip_selection(entries, ['name:Idle', 'name:Nope'], picked=True)
        self.assertEqual([row['order'] for row in selected], [1])
        self.assertEqual(catalog[0]['selection_provenance']['skipped_selectors'],
                         [dict(selector='name:Nope', reason='matches no source clip')])

    def test_two_rows_with_the_same_clip_bytes_resolve_to_the_first(self):
        entries = [source_row(0, 'Idle', 'hero'), source_row(1, 'Idle', 'npc')]
        selected, catalog = resolve_clip_selection(entries, ['name:Idle'], picked=True)
        self.assertEqual([row['order'] for row in selected], [0])
        self.assertEqual(catalog[0]['selection_provenance']['selection_mode'], 'picked')

    def test_without_picked_those_cases_raise(self):
        unsupported = [source_row(0, 'Idle', 'hero', status='unsupported')]
        with self.assertRaisesRegex(ClipSelectionError, 'non-supported'):
            resolve_clip_selection(unsupported, ['name:Idle'])
        with self.assertRaisesRegex(ClipSelectionError, 'No source clip matches'):
            resolve_clip_selection(unsupported, ['name:Nope'])
        same_bytes = [source_row(0, 'Idle', 'hero'), source_row(1, 'Idle', 'npc')]
        with self.assertRaisesRegex(ClipSelectionError, 'Ambiguous'):
            resolve_clip_selection(same_bytes, ['name:Idle'])


class SelectionMode(unittest.TestCase):
    def test_mode_is_picked_for_picked_selectors_explicit_otherwise_and_default_without_selectors(self):
        entries = [source_row(0, 'Idle', 'hero')]

        def mode(**options):
            _selected, catalog = resolve_clip_selection(entries, **options)
            return catalog[0]['selection_provenance']['selection_mode']

        self.assertEqual(mode(selectors=['name:Idle'], picked=True), 'picked')
        self.assertEqual(mode(selectors=['name:Idle']), 'explicit')
        self.assertEqual(mode(), 'default_all_supported')
        self.assertEqual(mode(picked=True), 'default_all_supported')


if __name__ == '__main__':
    unittest.main()
