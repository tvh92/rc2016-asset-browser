import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rc2016.pipeline.pilot_mounts import NEFARIOUS, NEFARIOUS_MECH, clip_suffix, pilot_mounts
from rc2016.pipeline.run import GROUP_STATUSES, assemble_group_blend


class PilotMountTests(unittest.TestCase):
    def test_pilot_mounts_only_with_its_vehicle(self):
        self.assertEqual(pilot_mounts([NEFARIOUS]), {})
        self.assertEqual(pilot_mounts([NEFARIOUS_MECH]), {})
        [(pilot, mount)] = pilot_mounts([NEFARIOUS_MECH, NEFARIOUS]).items()
        self.assertEqual((pilot, mount['owner'], mount['joint']), (NEFARIOUS, NEFARIOUS_MECH, 'chest'))
        self.assertEqual(mount['clips'], dict(suffix='_enm_nefarious', owner_suffix='_enm_nefarious_mech'))
        self.assertTrue(mount['evidence'].startswith('fitted'))

    def test_paths_match_case_and_separator_insensitively(self):
        paths = [NEFARIOUS.upper().replace('/', '\\'), NEFARIOUS_MECH]
        self.assertEqual(list(pilot_mounts(paths)), [paths[0]])
        self.assertEqual(clip_suffix('Character\\Enemy\\enm_Nefarious\\enm_Nefarious.model'), '_enm_nefarious')

    def test_group_member_rides_on_the_vehicle_joint(self):
        with tempfile.TemporaryDirectory() as tmp:
            finals = {}
            for path in (NEFARIOUS_MECH, NEFARIOUS):
                finals[path] = Path(tmp) / (Path(path).stem + '.blend')
                finals[path].write_bytes(path.encode())
            manifest = dict(models={k: dict(status=GROUP_STATUSES[0], model=k, final=str(v)) for k, v in finals.items()},
                            errors=[])
            captured = {}
            with patch('rc2016.pipeline.run.run_group_blend', lambda name, members, *a: captured.update(members=members)):
                assemble_group_blend(list(finals), manifest, SimpleNamespace(group_blend='mech', group_actors=''),
                                     lambda *a, **k: None)
        mech, pilot = captured['members']
        self.assertNotIn('mount', mech)
        self.assertEqual(pilot['mount'], dict(joint='chest', owner=mech['name'], origin='joint',
                                              clips=dict(suffix='_enm_nefarious', owner_suffix='_enm_nefarious_mech')))
        self.assertEqual(pilot['placement']['pilot'], 'Nefarious in his mech')
        self.assertNotIn('native_matrix', pilot)


if __name__ == '__main__':
    unittest.main()
