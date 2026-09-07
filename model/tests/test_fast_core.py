import random
import unittest
from azilla_cycle_model.core_iteration import CoreIteration
from azilla_cycle_model.fast_core import FastCoreIteration


def stimulus(seed=1, iterations=3):
    rng = random.Random(seed)
    oracle = CoreIteration(timing_only=True)
    def step(**args):
        oracle.tick(**args)
        return args
    yield step(rst=True)
    yield step(init_start=True,init_state=rng.getrandbits(32),noise_seed=rng.getrandbits(32))
    while oracle.core_state != oracle.IDLE:
        yield step(weight_init_valid=rng.random()>.3,weight_init_data=rng.getrandbits(256))
    for iteration in range(iterations):
        yield step(iter_start=True,coeff_a=rng.randrange(-100,100),coeff_b=-3)
        while not oracle.outputs().job_ready: yield step()
        for job in range(4):
            yield step(job_valid=True,source=job,transpose=bool(job%2))
            while oracle.off_state == oracle.STATE_REQ:
                yield step(state_req_ready=rng.random()>.7)
            while oracle.off_state == oracle.STATE_WAIT:
                yield step(state_rsp_valid=rng.random()>.7,state_rsp_data=rng.getrandbits(32))
            while oracle.off_state == oracle.LOAD:
                yield step(weight_valid=rng.random()>.4,weight_data=rng.getrandbits(256))
            while not oracle.outputs().job_done: yield step()
            yield step()
        for _ in range(rng.randrange(8)): yield step()
        yield step(partials_done=True)
        while not oracle.outputs().iter_done: yield step(noise_amplitude=7)
        for _ in range(rng.randrange(5)): yield step()
        yield step(commit=True,done=iteration==iterations-1)
        yield step()


class FastCoreTests(unittest.TestCase):
    def compare(self, args_list):
        ref,fast = CoreIteration(timing_only=True),FastCoreIteration()
        attributes = ('core_state','off_state','diagonal_captured','off_beat','source',
            'source_state','transpose','job_done','state_current','lfsr_state',
            'weight_beat_count','partials_done_pending','done_latched',
            'coeff_a_reg','coeff_b_reg','accumulator_total','accumulator_h0',
            'diagonal_result','directed_result')
        mvm_attributes=('active','process_row','request_row','read_valid','result',
                        'done','transpose_mode','weight_row')
        for cycle,args in enumerate(args_list):
            self.assertEqual(ref.tick(**args),fast.tick(**args),cycle)
            self.assertEqual(ref.outputs(),fast.outputs(),cycle)
            for attr in attributes:
                self.assertEqual(getattr(ref,attr),getattr(fast,attr),(cycle,attr))
            for attr in mvm_attributes:
                self.assertEqual(getattr(ref.local_mvm,attr),getattr(fast.local_mvm,attr),(cycle,attr))
            self.assertEqual(ref.sram.rows,fast.sram.rows,cycle)
            self.assertEqual(ref.sram.read_data,fast.sram.read_data,cycle)

    def test_complete_lifecycle_random_stalls(self):
        for seed in range(8): self.compare(stimulus(seed))

    def test_random_adversarial_control_and_resets(self):
        rng=random.Random(912)
        args=[]
        for _ in range(3000):
            args.append(dict(rst=rng.random()<.01,init_start=rng.random()<.05,
                iter_start=rng.random()<.03,partials_done=rng.random()<.02,
                commit=rng.random()<.05,done=rng.random()<.003,
                weight_init_valid=True,job_valid=rng.random()<.1,
                state_req_ready=rng.random()<.6,state_rsp_valid=rng.random()<.6,
                weight_valid=rng.random()<.8,noise_amplitude=13,noise_seed=1357,
                coeff_a=-17,coeff_b=8,transpose=bool(rng.getrandbits(1))))
        self.compare(args)

    def test_pending_done_during_final_job_drains_before_finalize(self):
        args=list(stimulus(41,1))
        ref=CoreIteration(timing_only=True)
        injected=False
        for inputs in args:
            if not injected and ref.source==3 and ref.off_state==ref.RUN:
                inputs['partials_done']=True
                injected=True
            ref.tick(**inputs)
        self.assertTrue(injected)
        self.compare(args)

    def test_reject_arithmetic(self):
        with self.assertRaises(ValueError): FastCoreIteration(timing_only=False)


if __name__ == '__main__':
    unittest.main()
