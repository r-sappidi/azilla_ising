import unittest

from azilla_cycle_model.adapters import NOC_STATE
from azilla_cycle_model.noc import Flit
from azilla_cycle_model.system import IsingMeshSystem
from azilla_cycle_model.workload import Geometry


class SystemTests(unittest.TestCase):
    def test_local_state_publication_reaches_cross_table(self):
        system = IsingMeshSystem(Geometry(1, 1, 1, 1))
        system.tick(rst=True)
        publication = Flit(
            data=0xA5A55A5A, packet_type=NOC_STATE,
            dest_x=0, dest_y=0, block_id=0,
        )
        held = publication
        for _ in range(8):
            output = system.tick(state_publications=[held])
            if held is not None and output.injection_ready[0]:
                held = None
        self.assertEqual(system.cross_nodes[0].node.state_mem[0], 0xA5A55A5A)


if __name__ == "__main__":
    unittest.main()
