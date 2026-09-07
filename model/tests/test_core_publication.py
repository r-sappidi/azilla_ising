import unittest
from azilla_cycle_model.core_publication import run_core_state_publication
from azilla_cycle_model.workload import Geometry, ScheduledBlock
from azilla_cycle_model.noc import Mesh, Flit


class CorePublicationTests(unittest.TestCase):
    def test_two_epochs_preserve_mesh_and_changed_payloads(self):
        geometry = Geometry(2,1,2,2)
        records = [ScheduledBlock(0,1),ScheduledBlock(0,2),ScheduledBlock(0,4)]
        mesh = Mesh(2,1,4)
        for epoch in range(2):
            words = {b:(epoch+1)*100+b for b in range(8)}
            events = []
            result = run_core_state_publication(geometry,records,mesh=mesh,
                states=words,epoch=epoch,start_cycle=epoch*100,
                observer=lambda *args:events.append(args))
            self.assertIs(result.mesh,mesh)
            self.assertEqual(result.elapsed_cycles,18)
            self.assertEqual(result.gathered_words,8)
            self.assertEqual(result.cache_write_words,6)
            self.assertEqual(result.cache_contents,
                ({b:words[b] for b in (0,1,2,4)}, {0:words[0]}, {0:words[0]}, {}))
            self.assertEqual(len(events),6)
            self.assertEqual({f.epoch for _,_,_,_,f in events},{epoch})

    def test_busy_mesh_rejected_without_discarding_data(self):
        mesh = Mesh(2,1,4)
        mesh.tick({0:Flit(dest_x=1)}, {0:True,1:True})
        with self.assertRaisesRegex(ValueError,"quiescent"):
            run_core_state_publication(Geometry(2,1,2,2),[],mesh=mesh)

    def test_contended_mesh_cache_payload_conservation(self):
        geometry = Geometry(2,2,2,2)
        records = [ScheduledBlock(a,b) for a in range(16) for b in range(a+1,16)]
        mesh = Mesh(2,2,4)
        for epoch in range(2):
            words = {b:epoch*1000+b for b in range(16)}
            phase = run_core_state_publication(geometry,records,mesh=mesh,
                states=words,epoch=epoch)
            self.assertEqual(phase.network.injected_flits,48)
            self.assertEqual(phase.network.ejected_flits,48)
            # Each H0 has two destinations: their mutual interaction plus all
            # other sources require every frozen block exactly once in cache.
            self.assertEqual(phase.cache_contents,tuple(dict(words) for _ in range(8)))


if __name__ == "__main__":
    unittest.main()
