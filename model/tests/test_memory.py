import unittest

from azilla_cycle_model.hierarchy import DmaCommand
from azilla_cycle_model.memory import (
    DramWeightStreamer,
    MemoryResponse,
)


class StreamerTests(unittest.TestCase):
    def test_tagged_out_of_order_reassembly(self):
        streamer = DramWeightStreamer(
            mvm_count=1, total_block_count=64,
            request_lanes=2, response_lanes=2,
        )
        command = DmaCommand(3, 4, 5, 9)
        streamer.tick(scheduler_commands=[command])

        requests = []
        for _ in range(40):
            before = streamer.tick(memory_request_ready=[True, True])
            requests.extend(request for request in before.memory_requests
                            if request is not None)
            if len(requests) == 32:
                break
        self.assertEqual(len(requests), 32)
        self.assertEqual(streamer.outputs().outstanding, 32)

        # Return reverse order to exercise tag-selected storage.
        reverse = list(reversed(requests))
        for index in range(0, 32, 2):
            streamer.tick(memory_responses=[
                MemoryResponse(reverse[index].tag, reverse[index].tag),
                MemoryResponse(reverse[index + 1].tag, reverse[index + 1].tag),
            ])
        self.assertTrue(streamer.outputs().node[0].command_valid)
        streamer.tick(node_command_ready=[True])
        observed = []
        for _ in range(32):
            output = streamer.outputs().node[0]
            observed.append(output.weight_data)
            streamer.tick(node_weight_ready=[True])
        expected = [streamer.encode_tag(0, 0, beat) for beat in range(32)]
        self.assertEqual(observed, expected)
        self.assertTrue(streamer.outputs().idle)


if __name__ == "__main__":
    unittest.main()
