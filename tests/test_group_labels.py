import unittest

from rc2016.pipeline.run import attach_owners, group_member_labels


class GroupMemberLabelTests(unittest.TestCase):
    def test_shared_leading_words_are_dropped(self):
        self.assertEqual(group_member_labels(['c/hero/a/hero_ratchet_bangle_boot_grind.model',
                                              'c/hero/b/hero_ratchet_bangle_boot_mag.model']),
                         ['grind.model', 'mag.model'])

    def test_a_single_member_keeps_its_file_name(self):
        self.assertEqual(group_member_labels(['c/hero/a/hero_ratchet_bangle_boot_grind.model']),
                         ['hero_ratchet_bangle_boot_grind.model'])

    def test_same_file_name_in_two_folders_keeps_full_paths(self):
        labels = group_member_labels(['c/x/hero_boot.model', 'c/y/hero_boot.model'])
        self.assertEqual(labels, ['c/x/hero_boot.model', 'c/y/hero_boot.model'])


class AttachOwnerTests(unittest.TestCase):
    BODY = 'c/enm/zurkon/zurkon.model'
    PART = 'c/enm/zurkon/bangle_set/zurkon_bangle_00_hatch_attach/zurkon_bangle_00_hatch_attach.model'

    def test_attach_part_maps_to_the_body_in_its_folder(self):
        self.assertEqual(attach_owners([self.BODY, self.PART]), {self.PART: self.BODY})

    def test_part_without_its_body_selected_is_left_alone(self):
        self.assertEqual(attach_owners([self.PART]), {})

    def test_body_matches_regardless_of_case_and_separators(self):
        self.assertEqual(attach_owners(['C\enm\Zurkon\zurkon.model', self.PART]),
                         {self.PART: 'C\enm\Zurkon\zurkon.model'})

    def test_two_models_in_the_body_folder_are_ambiguous(self):
        self.assertEqual(attach_owners([self.BODY, 'c/enm/zurkon/other.model', self.PART]), {})

    def test_pads_without_attach_in_the_name_follow_the_body_too(self):
        pads = 'c/wpn/thumper/bangle_set/thumper_bangle_v1_pads/thumper_bangle_v1_pads.model'
        body = 'c/wpn/thumper/thumper.model'
        self.assertEqual(attach_owners([body, pads]), {pads: body})

    def test_debris_under_bangle_set_is_not_a_part(self):
        for name in ('zurkon_bangle_00_chunk', 'zurkon_bangle_00_stump'):
            debris = f'c/enm/zurkon/bangle_set/{name}/{name}.model'
            self.assertEqual(attach_owners([self.BODY, debris]), {})


if __name__ == '__main__':
    unittest.main()
