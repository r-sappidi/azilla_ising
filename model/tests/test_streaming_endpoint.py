import random
import unittest
from azilla_cycle_model.streaming_core import DirectedMVM
from azilla_cycle_model.fixed import pack_lanes


class StreamingEndpointTest(unittest.TestCase):
    def test_rows_gaps_transpose_and_extrema(self):
        for seed in range(8):
            rng=random.Random(seed)
            weights=[[rng.randrange(-128,128) for _ in range(32)] for _ in range(32)]
            weights[0][0]=-128;weights[31][31]=127
            state=rng.getrandbits(32)
            for transpose in (False,True):
                m=DirectedMVM()
                m.tick(start=True,stream=True,transpose=transpose)
                for row in range(32):
                    for _ in range(rng.randrange(5)):
                        old=(m.process_row,list(m.result),m.done)
                        m.tick(row_valid=False,state=state,weight_data=rng.getrandbits(256))
                        self.assertEqual(old,(m.process_row,list(m.result),m.done))
                    m.tick(row_valid=True,state=state,weight_data=pack_lanes(weights[row],8))
                    self.assertEqual(m.done,row==31)
                expected=[sum((weights[c][r] if transpose else weights[r][c])*
                              (1 if state>>c&1 else -1) for c in range(32)) for r in range(32)]
                self.assertEqual(m.result,expected)

    def test_reset_discards_partial_job(self):
        m=DirectedMVM()
        m.tick(start=True,stream=True,transpose=True)
        m.tick(state=1,weight_data=pack_lanes([127]*32,8))
        m.tick(rst=True)
        self.assertFalse(m.active)
        self.assertFalse(m.done)
        self.assertEqual(m.result,[0]*32)


if __name__=='__main__':unittest.main()
