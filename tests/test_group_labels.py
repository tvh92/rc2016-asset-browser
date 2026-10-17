import unittest

from rc2016.pipeline.run import group_member_labels


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


if __name__ == '__main__':
    unittest.main()
